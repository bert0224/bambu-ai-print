"""拓竹工程 3MF 只读兼容性与保留盘检查；不替代实际导入、几何或排布验证。"""
from pathlib import Path
from xml.parsers import expat
import argparse
import hashlib
import json
import posixpath
import xml.etree.ElementTree as ET
import zipfile

CORE='http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PROD='http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
Q='{'+CORE+'}'
P='{'+PROD+'}'

def require(ok,message):
    if not ok:raise ValueError(message)

def canonical(node):
    require(node is not None,'缺少应存在的 XML 节点')
    return (node.tag,sorted(node.attrib.items()),(node.text or '').strip(),[canonical(c) for c in node])

def target_path(target,part=''):
    name=posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part),target))
    require(not name.startswith('../') and '\\' not in name,'无效包内路径：'+name)
    return name

def read_relationships(data,part=''):
    tree=ET.fromstring(data)
    expected=[target_path(n.attrib['Target'],part) for n in tree if n.tag=='{'+REL+'}Relationship' and n.get('Type','').endswith('/3dmodel')]
    found=[]
    parser=expat.ParserCreate()
    def start(name,attrs):
        typ=attrs.get('Type','')
        if name=='Relationship' and typ.startswith('http://schemas.microsoft.com/3dmanufacturing/') and typ.endswith('3dmodel'):
            found.append(target_path(attrs['Target'],part))
    parser.StartElementHandler=start;parser.Parse(data,True)
    require(found==expected,'关系标签与 Bambu 原始回调不兼容，检查 ns0:Relationship 等前缀')
    return found

def model_xml(data):
    tree=ET.fromstring(data)
    require(tree.tag==Q+'model' and tree.get('unit','millimeter')=='millimeter','仅支持毫米模型')
    parser=expat.ParserCreate();bad=[]
    core_names={'model','resources','object','mesh','vertices','vertex','triangles','triangle','components','component','build','item','metadata'}
    def start(name,attrs):
        if ':' in name and name.split(':')[-1] in core_names:bad.append(name)
        for key in attrs:
            if key.endswith(':path') and key!='p:path':bad.append(key)
    parser.StartElementHandler=start;parser.Parse(data,True)
    require(not bad,'模型核心标签或路径前缀不兼容：'+str(sorted(set(bad))))
    return tree

def inspect(path):
    with zipfile.ZipFile(path) as z:
        require(z.testzip() is None,'ZIP 校验失败')
        require(len(z.namelist())==len(set(z.namelist())),'ZIP 条目重名')
        contents={n:z.read(n) for n in z.namelist()}
    entry=read_relationships(contents['_rels/.rels'])
    require(len(entry)==1,'入口模型数量必须为 1')
    models={};objects={};queue=[entry[0]]
    while queue:
        part=queue.pop(0)
        if part in models:continue
        require(part in contents,'关系引用文件缺失：'+part)
        root=model_xml(contents[part]);models[part]=root
        for obj in root.findall(Q+'resources/'+Q+'object'):
            key=(part,obj.get('id'));require(key not in objects,'模型对象 ID 重复')
            objects[key]=obj
        relfile=posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
        if relfile in contents:queue.extend(read_relationships(contents[relfile],part))
    meshes={};active=set()
    def resolve(key):
        require(key in objects,'组件目标未发现：'+str(key));require(key not in active,'组件引用循环')
        if key in meshes:return meshes[key]
        active.add(key);obj=objects[key];deps={key};triangles=0
        mesh=obj.find(Q+'mesh')
        if mesh is not None:
            nv=len(mesh.findall(Q+'vertices/'+Q+'vertex'));faces=mesh.findall(Q+'triangles/'+Q+'triangle')
            require(nv>0 and len(faces)>0,'网格为空：'+str(key))
            for face in faces:
                require(all(0<=int(face.get(k,'-1'))<nv for k in ('v1','v2','v3')),'网格顶点索引越界')
            triangles=len(faces)
        for child in obj.findall(Q+'components/'+Q+'component'):
            cp=target_path(child.get(P+'path'),key[0]) if child.get(P+'path') else key[0]
            n,sub=resolve((cp,child.get('objectid')));triangles+=n;deps.update(sub)
        require(triangles>0,'对象没有可达网格：'+str(key));active.remove(key)
        meshes[key]=(triangles,deps);return meshes[key]
    builds=models[entry[0]].findall(Q+'build/'+Q+'item')
    require(bool(builds),'缺少构建实例')
    for item in builds:resolve((entry[0],item.get('objectid')))
    return {'contents':contents,'entry':entry[0],'models':models,'objects':objects,'meshes':meshes,'builds':builds}

def metadata(node,key):
    found=node.find(f"metadata[@key='{key}']");require(found is not None,'缺少元数据：'+key)
    return found.get('value')

def preserved(baseline,current,plate_ids):
    old=ET.fromstring(baseline['contents']['Metadata/model_settings.config'])
    new=ET.fromstring(current['contents']['Metadata/model_settings.config'])
    require(baseline['contents']['Metadata/project_settings.config']==current['contents']['Metadata/project_settings.config'],'全局打印配置发生改变')
    for pid in plate_ids:
        find=lambda tree:next((p for p in tree.findall('plate') if metadata(p,'plater_id')==str(pid)),None)
        a,b=find(old),find(new);require(canonical(a)==canonical(b),'保留盘成员或盘元数据改变：'+str(pid))
        for inst in a.findall('model_instance'):
            oid=metadata(inst,'object_id');idx=int(metadata(inst,'instance_id'))
            olditems=[i for i in baseline['builds'] if i.get('objectid')==oid]
            newitems=[i for i in current['builds'] if i.get('objectid')==oid]
            require(len(olditems)==len(newitems) and 0<=idx<len(olditems),'保留对象实例数改变')
            require(canonical(olditems[idx])==canonical(newitems[idx]),'保留对象摆放改变：'+oid)
            require(canonical(old.find(f"object[@id='{oid}']"))==canonical(new.find(f"object[@id='{oid}']")),'保留对象配置改变：'+oid)
            key=(baseline['entry'],oid)
            for dep in baseline['meshes'][key][1]:
                require(dep in current['objects'],'保留对象依赖缺失')
                require(canonical(baseline['objects'][dep])==canonical(current['objects'][dep]),'保留对象几何或引用改变：'+str(dep))
                if dep[0]!=baseline['entry']:
                    require(baseline['contents'][dep[0]]==current['contents'][dep[0]],'保留网格文件字节改变：'+dep[0])
            for tag in ['assemble']:
                aa=old.find(tag);bb=new.find(tag)
                oldnodes=[] if aa is None else [canonical(n) for n in aa if n.get('object_id')==oid]
                newnodes=[] if bb is None else [canonical(n) for n in bb if n.get('object_id')==oid]
                require(oldnodes==newnodes,'保留对象装配记录改变：'+oid)

def check(path,baseline=None,plates=()):
    result={'文件':str(path),'结构检查通过':False,'错误':[],
      '尚需验证':['实际网格闭合性与体积','所有盘的重复对象、相交、落板及工艺安全间距','真实 Bambu Studio 导入与切片']}
    try:
        current=inspect(path)
        if plates:
            require(baseline is not None,'保留盘检查缺少 --baseline');preserved(inspect(baseline),current,plates)
        result.update({'结构检查通过':True,'可达模型文件数':len(current['models']),'构建实例数':len(current['builds']),
          '已比较的保留盘':list(plates),'SHA256':hashlib.sha256(Path(path).read_bytes()).hexdigest()})
    except (ValueError,KeyError,IndexError,ET.ParseError,expat.ExpatError,zipfile.BadZipFile,OSError) as exc:
        result['错误'].append(str(exc))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('file',type=Path)
    parser.add_argument('--baseline',type=Path);parser.add_argument('--preserve-plate',type=int,action='append',default=[])
    args=parser.parse_args();result=check(args.file,args.baseline,args.preserve_plate)
    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['结构检查通过'] else 1)

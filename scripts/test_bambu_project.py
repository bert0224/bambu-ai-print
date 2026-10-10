"""验证关系标签兼容性与保留盘变更检测；合成样本不替代真实文件回归。"""
from pathlib import Path
import tempfile
import unittest
import zipfile
from check_bambu_project import check,CORE,PROD,REL

class ProjectChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bambu-project-test-')
        self.root=Path(self.temp.name);self.counter=0
        self.data={
          '_rels/.rels':f'<Relationships xmlns="{REL}"><Relationship Target="/3D/3dmodel.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Id="r1"/></Relationships>',
          '3D/_rels/3dmodel.model.rels':f'<Relationships xmlns="{REL}"><Relationship Target="/3D/Objects/a.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Id="r2"/></Relationships>',
          '3D/3dmodel.model':f'<model xmlns="{CORE}" xmlns:p="{PROD}" unit="millimeter"><resources><object id="2"><components><component p:path="/3D/Objects/a.model" objectid="1"/></components></object></resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 0"/></build></model>',
          '3D/Objects/a.model':f'<model xmlns="{CORE}" unit="millimeter"><resources><object id="1"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/></vertices><triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/><triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/></triangles></mesh></object></resources></model>',
          'Metadata/model_settings.config':'<config><object id="2"><metadata key="name" value="测试件"/></object><plate><metadata key="plater_id" value="1"/><model_instance><metadata key="object_id" value="2"/><metadata key="instance_id" value="0"/></model_instance></plate></config>',
          'Metadata/project_settings.config':'{"layer_height":"0.2"}'
        }

    def tearDown(self):self.temp.cleanup()

    def write(self,data=None):
        self.counter+=1;path=self.root/f'{self.counter}.3mf'
        with zipfile.ZipFile(path,'w') as z:
            for name,text in (data or self.data).items():z.writestr(name,text)
        return path

    def test_default_namespace(self):
        result=check(self.write());self.assertTrue(result['结构检查通过'],result)

    def test_prefixed_relationship_rejected(self):
        d=dict(self.data);key='3D/_rels/3dmodel.model.rels'
        d[key]=d[key].replace('xmlns=','xmlns:ns0=').replace('<Relationships','<ns0:Relationships').replace('</Relationships','</ns0:Relationships').replace('<Relationship ','<ns0:Relationship ')
        r=check(self.write(d));self.assertFalse(r['结构检查通过']);self.assertIn('前缀',r['错误'][0])

    def test_missing_submodel(self):
        d=dict(self.data);del d['3D/Objects/a.model'];self.assertFalse(check(self.write(d))['结构检查通过'])

    def test_wrong_component_id(self):
        d=dict(self.data);key='3D/3dmodel.model';d[key]=d[key].replace('objectid="1"','objectid="99"')
        self.assertFalse(check(self.write(d))['结构检查通过'])

    def test_empty_mesh(self):
        d=dict(self.data);key='3D/Objects/a.model';s=d[key];start=s.index('<triangles>');end=s.index('</triangles>')+len('</triangles>')
        d[key]=s[:start]+'<triangles/>'+s[end:];self.assertFalse(check(self.write(d))['结构检查通过'])

    def test_preserved_baseline(self):
        baseline=self.write();self.assertTrue(check(self.write(),baseline,[1])['结构检查通过'])

    def test_changed_placement(self):
        baseline=self.write();d=dict(self.data);key='3D/3dmodel.model';d[key]=d[key].replace('1 0 0 0 1 0 0 0 1 0 0 0','1 0 0 0 1 0 0 0 1 10 0 0')
        r=check(self.write(d),baseline,[1]);self.assertFalse(r['结构检查通过']);self.assertIn('摆放',r['错误'][0])

    def test_changed_shared_mesh(self):
        baseline=self.write();d=dict(self.data);key='3D/Objects/a.model';d[key]=d[key].replace('x="1"','x="2"')
        r=check(self.write(d),baseline,[1]);self.assertFalse(r['结构检查通过']);self.assertIn('几何',r['错误'][0])

    def test_changed_global_profile(self):
        baseline=self.write();d=dict(self.data);d['Metadata/project_settings.config']='{"layer_height":"0.4"}'
        self.assertFalse(check(self.write(d),baseline,[1])['结构检查通过'])

if __name__=='__main__':unittest.main()

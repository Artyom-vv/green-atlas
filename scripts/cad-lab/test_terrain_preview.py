"""Geometry invariants for the isolated terrain experiment."""
import importlib.util
from pathlib import Path
import unittest

from shapely.geometry import Polygon

spec=importlib.util.spec_from_file_location('terrain_preview',Path(__file__).with_name('build_terrain_preview.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def packet():
    xy=[(0,0),(10,0),(10,10),(0,10)]
    points=[{'handle':str(i),'xyz':[x,y,0], 'nearby_labels':[{'handle':'t'+str(i)}]} for i,(x,y) in enumerate(xy)]
    texts=[{'handle':'t'+str(i),'value':150+x*.01+y*.02,'insert':[x+.2,y,0],
            'align_point':None,'halign':0,'valign':0} for i,(x,y) in enumerate(xy)]
    return {'pickets':points,'labels':texts,'breakline_candidates':[], 'bbox':[0,0,10,10], 'sha256':'synthetic'}


class TerrainPreviewTest(unittest.TestCase):
    def test_source_heights_are_preserved(self):
        p=packet();r=m.build(p)
        self.assertEqual(len(r['triangles']),2)
        self.assertAlmostEqual(r['surface_area_xy'],100)
        self.assertEqual([v['xyz'][2] for v in r['vertices']],[t['value'] for t in p['labels']])
        self.assertEqual(r['status'],'experimental_not_admitted')

    def test_sampling_does_not_invent_zero_outside_coverage(self):
        r=m.build(packet())
        self.assertAlmostEqual(m.sample_height(r,5,5),150.15)
        self.assertIsNone(m.sample_height(r,20,20))
        self.assertAlmostEqual(m.sample_height(r,0,0),150)
        r['triangles']=[]
        self.assertIsNone(m.sample_height(r,5,5))

    def test_breakline_prevents_crossing(self):
        p=packet();p['breakline_candidates']=[{'type':'LINE','start':[5,-1,0],'end':[5,11,0]}]
        r=m.build(p)
        self.assertEqual(len(r['vertices']),4)
        self.assertEqual(r['triangles'],[])
        self.assertEqual(r['excluded_triangles']['crosses_curb_or_slope'],2)

    def test_pair_not_averaged(self):
        p=packet();p['pickets'][0]['nearby_labels'].append({'handle':'paired'})
        r=m.build(p)
        self.assertNotIn('0',[v['picket'] for v in r['vertices']])

    def test_alignment_anchor_and_competing_picket(self):
        p=packet();p['labels'][0].update(insert=[100,100,0],align_point=[.2,0,0],valign=3)
        self.assertEqual(len(m.build(p)['vertices']),4)
        p['pickets'].append({'handle':'competing','xyz':[.5,0,0],'nearby_labels':[]})
        self.assertNotIn('0',[v['picket'] for v in m.build(p)['vertices']])

    def test_curved_breakline_keeps_bulge(self):
        r=m.blockers([{'type':'LWPOLYLINE','vertices':[(0,0,1),(10,0,0)],'closed':False}])
        self.assertLess(r.bounds[1],-4.9)
        self.assertGreater(r.length,15.6)

    def test_real_packet_invariants(self):
        import json
        path=Path('.runtime/terrain-control-20260918/packet/control.json')
        if not path.exists():self.skipTest('local dataset is not present')
        p=json.loads(path.read_text());r=m.build(p);b=m.blockers(p['breakline_candidates'])
        self.assertGreater(len(r['triangles']),0)
        for f in r['triangles']:
            self.assertFalse(Polygon([r['vertices'][i]['xyz'][:2] for i in f]).intersects(b))
        values={t['handle']:t['value'] for t in p['labels']}
        for v in r['vertices']:self.assertEqual(v['xyz'][2],values[v['label']])


if __name__=='__main__':unittest.main()

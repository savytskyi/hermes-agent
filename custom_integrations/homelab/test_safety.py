from pathlib import Path
import sys, unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from homelab import arr, grafana, influxdb, qbittorrent, homeassistant, servarr, grocy, frigate
from homelab.common import HomelabError

class SafetyTests(unittest.TestCase):
    def test_flux_name_rejects_injection(self):
        with self.assertRaises(ValueError): influxdb._name('x") |> drop()', 'sensor')

    def test_flux_time_rejects_injection(self):
        with self.assertRaises(ValueError): influxdb._time('now()) |> drop()', '-30d')

    def test_influx_csv_annotations_and_repeated_headers(self):
        text='''#datatype,string,long,dateTime:RFC3339,double\n,result,table,_time,_value\n,,0,2026-01-01T00:00:00Z,1.5\n\n,result,table,_time,_value\n,,1,2026-01-02T00:00:00Z,2.5\n'''
        rows=influxdb._parse_csv(text)
        self.assertEqual([r['_value'] for r in rows],['1.5','2.5'])
        self.assertNotIn('_value',[r.get('_value') for r in rows])

    @patch('homelab.arr._post', side_effect=AssertionError('write called'))
    @patch('homelab.arr._get')
    def test_radarr_preview_never_writes(self, get, post):
        get.return_value=[{'title':'Dune: Part Two','year':2024,'tmdbId':693134}]
        for value in (False,None,'false',0):
            out=arr.add_movie({'title':'Dune Part Two','confirm':value})
            self.assertFalse(out['write_performed'])
        post.assert_not_called()

    @patch('homelab.arr._post', side_effect=AssertionError('write called'))
    @patch('homelab.arr._get')
    def test_sonarr_preview_never_writes(self, get, post):
        get.return_value=[{'title':'Severance','year':2022,'tvdbId':371980}]
        out=arr.add_series({'title':'Severance','confirm':False})
        self.assertFalse(out['write_performed']); post.assert_not_called()

    def test_provider_id_mismatch_fails_closed(self):
        with self.assertRaises(HomelabError):
            arr._choose([{'title':'Dune','tmdbId':1}],'Dune',None,'tmdbId',2)

    def test_search_now_requires_boolean(self):
        with self.assertRaises(ValueError): arr._search_now({'search_now':'false'})

    @patch('homelab.qbittorrent.json_request')
    def test_qbittorrent_omits_tracker_and_filters_server_side(self, req):
        req.return_value=[{'name':'x','hash':'h','state':'stalledDL','tracker':'https://private.invalid/PASSKEY/announce'}]
        out=qbittorrent.execute({'action':'stalled','limit':10})
        self.assertNotIn('tracker',out['torrents'][0])
        self.assertEqual(req.call_args.kwargs['params'],{'filter':'stalled'})

    def test_grafana_redacts_sensitive_dashboard_keys(self):
        out=grafana._redact({'title':'ok','nested':{'apiKey':'secret','password':'secret'}})
        self.assertEqual(out['title'],'ok')
        self.assertEqual(out['nested']['apiKey'],'[REDACTED]')
        self.assertEqual(out['nested']['password'],'[REDACTED]')

    @patch('homelab.homeassistant.json_request', side_effect=AssertionError('write called'))
    @patch('homelab.homeassistant._allowlist', return_value={'light.desk'})
    def test_ha_preview_never_calls_service(self, allowlist, request):
        out=homeassistant._control({'entity_id':'light.desk','service':'turn_on','confirm':False})
        self.assertFalse(out['write_performed']); request.assert_not_called()

    @patch('homelab.homeassistant._allowlist', return_value=set())
    def test_ha_unallowlisted_entity_fails_closed(self, allowlist):
        with self.assertRaises(HomelabError):
            homeassistant._control({'entity_id':'light.desk','service':'turn_on','confirm':True})

    @patch('homelab.servarr._post', side_effect=AssertionError('write called'))
    @patch('homelab.servarr._lidarr_lookup', return_value=[{'artistName':'Massive Attack','foreignArtistId':'x'}])
    def test_lidarr_preview_never_writes(self, lookup, post):
        out=servarr.lidarr_add({'artist':'Massive Attack','confirm':False})
        self.assertFalse(out['write_performed']); post.assert_not_called()

    @patch('homelab.grocy._run', side_effect=AssertionError('write called'))
    def test_grocy_preview_never_writes(self, run):
        out=grocy.execute({'action':'consume','item':'Milk','confirm':False})
        self.assertFalse(out['write_performed']); run.assert_not_called()

    @patch('homelab.frigate._session_origin', return_value=('telegram','999'))
    @patch('homelab.frigate._allowed_chat_ids', return_value={'123'})
    def test_frigate_rejects_unauthorized_chat(self, allowed, origin):
        with self.assertRaises(HomelabError): frigate.authorize('session')

if __name__=='__main__': unittest.main()

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import house_audio_server as h
from subwoofers import Subwoofers, SubwooferError
from test_controllers import NodeMonitor
from test_house_audio_server import FakeClock, FakeMpd
import test_radio


class SubwooferTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'subs.json'
        self.subs = Subwoofers(self.path, clock=self.clock)
        self.monitor = NodeMonitor()
        for rid in ('bose', 'garage-sub', 'kitchen', 'music', 'garage', 'phone-audio'):
            self.monitor.set(rid, present=False, audible=False)
        self.controllers = h.ControllerRegistry(clock=self.clock, subwoofers=self.subs)
        self.config = self.subs.save(dict(name='Bose', rendererId='bose',
                                         parentRendererIds=['kitchen', 'music']), self.monitor.snapshot())

    def demand(self, config=None):
        snap = self.monitor.snapshot()
        return self.subs.demand((config or self.config)['id'], self.controllers.snapshot(snap))

    def test_assignments_persist_and_use_any_selected_parent(self):
        self.assertEqual(Subwoofers(self.path).renderer_ids(), {'bose'})
        self.assertFalse(self.demand()['needed'])
        self.monitor.set('kitchen')
        self.assertTrue(self.demand()['needed'])
        self.monitor.set('music')
        self.monitor.set('kitchen', present=False)
        self.assertEqual(self.demand()['matchingRendererIds'], ['music'])
        self.monitor.set('music', present=False)
        self.monitor.set('garage')
        self.assertFalse(self.demand()['needed'])
        self.assertEqual(self.demand()['standbyDelaySeconds'], 30)

    def test_multiple_subs_and_overlapping_assignments(self):
        other = self.subs.save(dict(name='Garage bass', rendererId='garage-sub',
                                   parentRendererIds=['music', 'garage']), self.monitor.snapshot())
        self.monitor.set('bose'); self.monitor.set('garage-sub')
        self.assertFalse(self.demand()['needed']); self.assertFalse(self.demand(other)['needed'])
        self.monitor.set('music')
        self.assertTrue(self.demand()['needed']); self.assertTrue(self.demand(other)['needed'])
        self.monitor.set('music', present=False); self.monitor.set('garage')
        self.assertFalse(self.demand()['needed']); self.assertTrue(self.demand(other)['needed'])

    def test_reject_self_dependency_cross_dependency_duplicates_and_unknown_nodes(self):
        cases = [dict(name='Bose', rendererId='bose', parentRendererIds=['kitchen']),
                 dict(name='Self', rendererId='garage-sub', parentRendererIds=['garage-sub']),
                 dict(name='Other', rendererId='garage-sub', parentRendererIds=['bose']),
                 dict(name='Other', rendererId='music', parentRendererIds=['garage']),
                 dict(name='Unknown', rendererId='invented', parentRendererIds=['garage']),
                 dict(name='Other', rendererId='garage-sub', parentRendererIds=[]),
                 dict(name='Other', rendererId='garage-sub', parentRendererIds=['garage', 'garage'])]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(SubwooferError):
                self.subs.save(item, self.monitor.snapshot())
        self.assertEqual(self.subs.renderer_ids(), {'bose'})

    def test_edit_preserves_identity_and_allows_existing_assignment_when_monitor_down(self):
        changed = dict(self.config, name='New name', parentRendererIds=['music'])
        self.subs.save(changed, {'reachable':False, 'clients':[]})
        self.assertEqual(Subwoofers(self.path)._configs[0], changed)
        with self.assertRaises(SubwooferError):
            self.subs.save(dict(changed,rendererId='garage-sub'), self.monitor.snapshot())

    def test_write_failure_keeps_original_assignments(self):
        previous = self.path.read_bytes()
        with patch('state_file.os.replace', side_effect=OSError('full')):
            with self.assertRaises(SubwooferError):
                self.subs.save(dict(self.config, name='Changed'), self.monitor.snapshot())
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(self.subs._configs[0]['name'], 'Bose')
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    def test_corrupt_config_fails_instead_of_making_sub_passive(self):
        self.path.write_text('{bad')
        with self.assertRaises(ValueError):
            Subwoofers(self.path)

    def test_present_paused_or_muted_passive_speaker_still_demands_sub(self):
        self.monitor.set('music', audible=False)
        self.assertTrue(self.demand()['needed'])

    def test_control_only_or_muted_phone_does_not_demand_sub(self):
        self.subs.save(dict(self.config,parentRendererIds=['phone-audio']), self.monitor.snapshot())
        lease = self.controllers.attach(dict(controllerId='phone', rendererId='phone-audio',
                                            outputMuted=True, outputReady=False))
        self.monitor.set('phone-audio')
        self.assertFalse(self.demand()['needed'])
        self.controllers.heartbeat(dict(controllerId='phone', leaseId=lease['leaseId'], sequence=1,
                                        outputMuted=False, outputReady=True))
        self.assertTrue(self.demand()['needed'])
        self.controllers.detach(dict(controllerId='phone',leaseId=lease['leaseId']))
        self.assertFalse(self.demand()['needed'])

    def test_outage_is_unknown_and_stale_client_absence_is_false(self):
        self.monitor.set('music')
        self.monitor.reachable=False
        self.assertIsNone(self.demand()['needed'])
        self.assertFalse(self.demand()['known'])
        self.monitor.reachable=True
        self.monitor.set('music',present=False)
        self.assertFalse(self.demand()['needed'])

    def test_feedback_is_separate_fresh_and_not_persisted(self):
        def status():
            snap=self.monitor.snapshot()
            return self.subs.snapshot(snap,self.controllers.snapshot(snap))['subwoofers'][0]['status']
        self.assertFalse(status()['fresh'])
        self.subs.report(self.config['id'],dict(rendererId='bose', mode='auto',state='standby_pending',
                                               standbyRemainingSeconds=30))
        self.clock.advance(6)
        self.assertEqual(status()['standbyRemainingSeconds'],24)
        self.clock.advance(10)
        self.assertEqual(status()['state'],'unknown')
        self.assertIsNone(status()['mode'])
        self.monitor.set('music')
        self.subs.report(self.config['id'],dict(rendererId='bose',mode='force_standby',state='standby',acknowledged=True))
        self.assertTrue(self.demand()['needed'])
        self.assertTrue(status()['acknowledged'])
        restored=Subwoofers(self.path)
        self.assertFalse(restored._status(self.config['id'])['fresh'])
        with self.assertRaises(SubwooferError):
            self.subs.report(self.config['id'],dict(rendererId='other',mode='auto',state='awake'))
        for bad in (dict(mode='bad',state='awake'),dict(mode='auto',state='standby_pending',standbyRemainingSeconds=float('nan')),
                    dict(mode='force_awake',state='standby_pending',standbyRemainingSeconds=10),
                    dict(mode='auto',state='unknown',acknowledged=True)):
            with self.assertRaises(SubwooferError):
                self.subs.report(self.config['id'],dict(rendererId='bose',**bad))

    def test_sub_alone_never_starts_or_resumes_session(self):
        self.monitor.set('bose')
        for transport in ('stop','pause'):
            mpd=FakeMpd();mpd.state_data['transport']=transport
            policy=h.PassiveSessionPolicy(self.monitor,mpd_factory=lambda:mpd,controllers=self.controllers)
            policy._tick()
            self.assertEqual(mpd.commands,[])
            presence=self.controllers.snapshot(self.monitor.snapshot())
            self.assertEqual((presence['presentCount'],presence['passiveCount'],presence['audibleCount']), (0,0,0))
            self.assertTrue(self.monitor.snapshot()['clients'][0]['present'])

    def test_normal_departure_drains_despite_sub_and_sub_cannot_cancel_drain(self):
        self.monitor.set('music');self.monitor.set('bose')
        mpd=FakeMpd()
        policy=h.PassiveSessionPolicy(self.monitor,mpd_factory=lambda:mpd,controllers=self.controllers)
        policy._tick();self.monitor.set('music',present=False);policy._tick()
        self.assertTrue(policy.snapshot()['pendingFinalStop'])
        self.monitor.set('bose',present=False);policy._tick();self.monitor.set('bose');policy._tick()
        self.assertTrue(policy.snapshot()['pendingFinalStop'])
        self.monitor.set('music');policy._tick()
        self.assertFalse(policy.snapshot()['pendingFinalStop'])

    def test_sub_binding_cannot_count_as_controller_presence(self):
        self.controllers.attach(dict(controllerId='test',rendererId='bose',outputReady=True,outputMuted=False))
        self.monitor.set('bose')
        presence=self.controllers.snapshot(self.monitor.snapshot())
        self.assertEqual((presence['controllerCount'],presence['presentCount']), (0,0))

    def test_monitor_stalled_poll_becomes_unknown(self):
        monitor=h.SnapcastMonitor()
        monitor._apply_status({'groups':[], 'streams':[]})
        with patch.object(h.time,'monotonic',return_value=monitor._snapshot_received_at+6):
            self.assertFalse(monitor.snapshot()['reachable'])


class SubwooferHttpTests(unittest.TestCase):
    request=test_radio.RadioHttpTests.request
    add_station=test_radio.RadioHttpTests.add_station
    play_radio=test_radio.RadioHttpTests.play_radio
    def setUp(self):
        test_radio.RadioHttpTests.setUp(self)
        self.subs=Subwoofers(Path(self.directory.name)/'subs.json',clock=self.clock)
        self.controllers.subwoofers=self.subs
        p=patch.object(h,'SUBWOOFERS',self.subs);p.start();self.addCleanup(p.stop)
        self.monitor.set('bose');self.monitor.set('kitchen')

    def register(self):
        code,body=self.request('/api/subwoofers',dict(name='Bose',rendererId='bose',parentRendererIds=['kitchen']))
        self.assertEqual(code,200,body)
        return body['subwoofer']['id']

    def test_config_demand_status_round_trip_without_mpd(self):
        with patch.object(self.mpd,'state',side_effect=h.MpdError('offline')):
            sub_id=self.register()
            code,body=self.request('/api/subwoofers/'+sub_id+'/demand')
            self.assertEqual(code,200);self.assertTrue(body['needed']);self.assertEqual(body['standbyDelaySeconds'],30)
            code,body=self.request('/api/subwoofers/'+sub_id+'/status',dict(rendererId='bose',mode='auto',state='standby',acknowledged=False))
            self.assertEqual(code,200)
            code,body=self.request('/api/subwoofers')
            self.assertTrue(body['subwoofers'][0]['demand']['needed'])
            self.assertEqual(body['subwoofers'][0]['status']['state'],'standby')
            self.assertFalse(body['subwoofers'][0]['status']['acknowledged'])
            self.monitor.reachable=False
            code,body=self.request('/api/subwoofers/'+sub_id+'/demand')
            self.assertIsNone(body['needed'])
        code,_=self.request('/api/subwoofers/not-registered/demand')
        self.assertEqual(code,404)

    def test_final_normal_radio_node_departure_ends_session_with_sub_still_connected(self):
        self.register();self.play_radio()
        self.policy._tick()
        self.monitor.set('kitchen',present=False)
        self.policy._tick()
        self.assertIsNone(self.radio.station)
        self.assertEqual(self.mpd.queue_files,[])

    def test_page_assets_and_cross_origin_edit_rejection(self):
        import http.client
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=3)
        self.addCleanup(connection.close)
        for path,content_type in [('/subwoofers','text/html'),('/subwoofers.js','text/javascript'),('/subwoofers.css','text/css')]:
            connection.request('GET',path);response=connection.getresponse();body=response.read()
            self.assertEqual(response.status,200);self.assertIn(content_type,response.getheader('Content-Type'))
            self.assertTrue(body);self.assertIn("frame-ancestors 'none'",response.getheader('Content-Security-Policy'))
        connection.request('POST','/api/subwoofers',body='{}',headers={'Content-Type':'application/json','Origin':'https://unrelated.example'})
        response=connection.getresponse();response.read();self.assertEqual(response.status,403)
        connection.request('POST','/api/subwoofers',body='{}',headers={'Content-Type':'text/plain'})
        response=connection.getresponse();response.read();self.assertEqual(response.status,415)

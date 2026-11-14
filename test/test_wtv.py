import copy
import io
import unittest
from unittest.mock import patch

from test.helper import FakeYDL
from yt_dlp.extractor import gen_extractors
from yt_dlp.extractor.wtv import WTVChannelIE, WTVIE
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError, UserNotLive


class TestWTV(unittest.TestCase):
    URL = 'https://w.tv/example/videos/01a1c4fb-f8e2-72be-9f9c-08e726da7b9c'
    ID = '01a1c4fb-f8e2-72be-9f9c-08e726da7b9c'
    STREAM = {
        'streamId': ID,
        'title': 'Example replay',
        'description': 'Public description',
        'state': 'finished',
        'playbackUrl': 'https://cdn.example/master.m3u8?token=fresh',
        'thumbnailUrl': 'https://cdn.example/thumb.jpg',
        'startedAt': '2026-11-10T10:23:30.017Z',
        'views': 12,
        'channel': {'name': 'example', 'channelId': '01a09c19-6d20-719f-abb3-ea28926fb201'},
        'subcategory': {'name': 'Example category'},
    }
    MANIFEST = b'''#EXTM3U
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",URI="subs.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2",SUBTITLES="subs"
360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",SUBTITLES="subs"
720.m3u8
'''

    def setUp(self):
        self.ie = WTVIE(FakeYDL())

    def test_matching_and_registration(self):
        for suffix in ('', '/', '?source=example', '#player'):
            self.assertTrue(WTVIE.suitable(self.URL + suffix))
        for url in ('https://w.tv/example', 'https://w.tv/example/videos',
                    self.URL + '/extra', self.URL + 'a', self.URL.replace('w.tv', 'evil.example')):
            self.assertFalse(WTVIE.suitable(url))
        self.assertEqual([ie.IE_NAME for ie in gen_extractors()
                          if ie.suitable(self.URL) and ie.IE_NAME != 'generic'], ['w.tv'])

    def test_metadata_and_native_hls_parsing(self):
        with patch.object(self.ie, '_download_json', return_value={'stream': self.STREAM}) as metadata, \
                patch.object(self.ie, '_request_webpage', return_value=Response(
                    io.BytesIO(self.MANIFEST), 'https://cdn.example/master.m3u8?token=fresh',
                    {'Content-Type': 'application/vnd.apple.mpegurl'})) as manifest:
            result = self.ie._real_extract(self.URL)
        self.assertEqual(metadata.call_args.args, (
            f'https://streams-search-service.w.tv/api/v1/streams/{self.ID}', self.ID))
        self.assertEqual(manifest.call_args.args[0], self.STREAM['playbackUrl'])
        self.assertEqual(result['id'], self.ID)
        self.assertEqual(result['title'], 'Example replay')
        self.assertEqual(result['channel_id'], '01a09c19-6d20-719f-abb3-ea28926fb201')
        self.assertEqual(result['channel_url'], 'https://w.tv/example')
        self.assertEqual(result['timestamp'], 1794306210)
        self.assertEqual(result['view_count'], 12)
        self.assertEqual(result['categories'], ['Example category'])
        self.assertEqual([f['height'] for f in result['formats']], [360, 720])
        self.assertEqual(result['subtitles']['en'][0]['url'], 'https://cdn.example/subs.m3u8')

    def test_optional_metadata_and_fresh_playback(self):
        with patch.object(self.ie, '_download_json', side_effect=[
                {'stream': {'playbackUrl': f'https://cdn.example/{n}.m3u8'}} for n in (1, 2)]), \
                patch.object(self.ie, '_extract_m3u8_formats_and_subtitles', return_value=([], {})) as hls:
            for _ in range(2):
                self.assertEqual(self.ie._real_extract(self.URL)['title'], self.ID)
        self.assertEqual([c.args[0] for c in hls.call_args_list], [
            'https://cdn.example/1.m3u8', 'https://cdn.example/2.m3u8'])

    def test_unavailable_replays(self):
        for response in ({}, {'stream': None}, {'stream': {'state': 'live'}},
                         {'stream': {'state': 'finished'}}, {'stream': {'playbackUrl': 'not a URL'}}):
            with self.subTest(response=response), \
                    patch.object(self.ie, '_download_json', return_value=response), \
                    patch.object(self.ie, '_extract_m3u8_formats_and_subtitles') as hls:
                with self.assertRaises(ExtractorError) as cm:
                    self.ie._real_extract(self.URL)
                self.assertTrue(cm.exception.expected)
                hls.assert_not_called()

    def test_manifest_failure_is_not_hidden(self):
        stream = copy.deepcopy(self.STREAM)
        with patch.object(self.ie, '_download_json', return_value={'stream': stream}), \
                patch.object(self.ie, '_extract_m3u8_formats_and_subtitles',
                             side_effect=ExtractorError('Manifest unavailable')):
            with self.assertRaisesRegex(ExtractorError, 'Manifest unavailable'):
                self.ie._real_extract(self.URL)


class TestWTVChannel(unittest.TestCase):
    PROFILE = {'profile': {'userId': '01a09c19-6d20-719f-abb3-ea28926fb201', 'nickname': 'Example'}}

    def setUp(self):
        self.ie = WTVChannelIE(FakeYDL())

    def test_url_scope(self):
        for url in ('https://w.tv/example', 'https://w.tv/example/videos/', 'https://w.tv/example?x=1'):
            self.assertTrue(WTVChannelIE.suitable(url))
            self.assertFalse(WTVIE.suitable(url))
        self.assertFalse(WTVChannelIE.suitable(TestWTV.URL))
        self.assertFalse(WTVChannelIE.suitable('https://w.tv/example/videos/extra'))

    def test_archive_delegation_deduplication_and_live_exclusion(self):
        finished = TestWTV.STREAM
        live = {**finished, 'state': 'started'}
        with patch.object(self.ie, '_download_json', side_effect=[
                self.PROFILE, {'data': [finished, finished, live]}]) as request:
            result = self.ie._real_extract('https://w.tv/example/videos')
        self.assertEqual(result['id'], '01a09c19-6d20-719f-abb3-ea28926fb201')
        self.assertEqual(result['title'], 'Example - Videos')
        self.assertEqual(len(result['entries']), 1)
        self.assertEqual(result['entries'][0]['url'], TestWTV.URL)
        self.assertEqual(result['entries'][0]['ie_key'], WTVIE.ie_key())
        self.assertEqual(request.call_args.kwargs['query'], {'limit': 100})

    def test_empty_archive(self):
        with patch.object(self.ie, '_download_json', side_effect=[self.PROFILE, {'data': []}]):
            self.assertEqual(self.ie._real_extract('https://w.tv/example/videos')['entries'], [])

    def test_continuation_is_never_silently_ignored(self):
        for key in ('cursor', 'nextCursor', 'next', 'hasMore'):
            with self.subTest(key=key), patch.object(self.ie, '_download_json', side_effect=[
                    self.PROFILE, {'data': [], key: 'opaque'}]) as request:
                with self.assertRaisesRegex(ExtractorError, 'unsupported continuation'):
                    self.ie._real_extract('https://w.tv/example/videos')
                self.assertEqual(request.call_count, 2)

    def test_limit_warning(self):
        with patch.object(self.ie, '_download_json', side_effect=[
                self.PROFILE, {'data': [TestWTV.STREAM] * 100}]), \
                patch.object(self.ie, 'report_warning') as warning:
            self.ie._real_extract('https://w.tv/example/videos')
        warning.assert_called_once()

    def test_unavailable_and_invalid_archive(self):
        for response in ({}, {'data': None}, {'data': [None]},
                         {'data': [{'state': 'finished', 'streamId': 'invalid'}]},
                         {'data': [{'state': 'unknown'}]}):
            with self.subTest(response=response), patch.object(self.ie, '_download_json', side_effect=[
                    self.PROFILE, response]):
                with self.assertRaises(ExtractorError):
                    self.ie._real_extract('https://w.tv/example/videos')

    def test_missing_profile(self):
        with patch.object(self.ie, '_download_json', return_value={}):
            with self.assertRaisesRegex(ExtractorError, 'Channel is unavailable'):
                self.ie._real_extract('https://w.tv/example')

    def test_offline_channel_does_not_become_archive(self):
        with patch.object(self.ie, '_download_json', side_effect=[self.PROFILE, {'channel': {'live': False}}]):
            with self.assertRaises(UserNotLive):
                self.ie._real_extract('https://w.tv/example')

    def test_live_uses_shared_parser_and_sets_live_flag(self):
        stream = {**TestWTV.STREAM, 'state': 'started'}
        with patch.object(self.ie, '_download_json', side_effect=[self.PROFILE, {
                'channel': {'live': True, 'liveStreamId': TestWTV.ID, 'liveStream': stream}}]), \
                patch.object(self.ie, '_extract_m3u8_formats_and_subtitles', return_value=([], {})) as hls:
            result = self.ie._real_extract('https://w.tv/example')
        self.assertTrue(result['is_live'])
        self.assertEqual(result['id'], TestWTV.ID)
        self.assertTrue(hls.call_args.kwargs['live'])


class TestWTVRobustness(unittest.TestCase):
    @staticmethod
    def http_error(status):
        return ExtractorError('Synthetic HTTP failure', cause=HTTPError(Response(
            io.BytesIO(b''), 'https://example.invalid/resource', {}, status=status)))

    def test_api_errors_are_actionable_without_retrying(self):
        for status, message in ((401, 'authentication'), (403, 'denied'),
                                (404, 'not found'), (410, 'no longer'), (429, 'rate limit')):
            for ie, url in ((WTVIE(FakeYDL()), TestWTV.URL),
                            (WTVChannelIE(FakeYDL()), 'https://w.tv/example/videos')):
                with self.subTest(status=status, url=url), \
                        patch.object(ie, '_download_json', side_effect=self.http_error(status)) as request:
                    with self.assertRaisesRegex(ExtractorError, message) as cm:
                        ie._real_extract(url)
                    self.assertTrue(cm.exception.expected)
                    self.assertEqual(cm.exception.cause.status, status)
                    request.assert_called_once()

    def test_server_and_transport_errors_are_preserved(self):
        ie = WTVIE(FakeYDL())
        for error in (self.http_error(500), ExtractorError('Connection timed out')):
            with self.subTest(error=error), patch.object(ie, '_download_json', side_effect=error):
                with self.assertRaises(ExtractorError) as cm:
                    ie._real_extract(TestWTV.URL)
                self.assertIs(cm.exception, error)

    def test_expired_manifest_has_diagnostic_and_next_extraction_refreshes(self):
        ie = WTVIE(FakeYDL())
        old = {**TestWTV.STREAM, 'playbackUrl': 'https://cdn.example/old.m3u8'}
        new = {**TestWTV.STREAM, 'playbackUrl': 'https://cdn.example/new.m3u8'}
        with patch.object(ie, '_download_json', side_effect=[{'stream': old}, {'stream': new}]) as request, \
                patch.object(ie, '_extract_m3u8_formats_and_subtitles',
                             side_effect=[self.http_error(403), ([], {})]) as hls:
            with self.assertRaisesRegex(ExtractorError, 'may have expired') as cm:
                ie._real_extract(TestWTV.URL)
            self.assertTrue(cm.exception.expected)
            self.assertEqual(request.call_count, 1)
            ie._real_extract(TestWTV.URL)
            self.assertEqual(request.call_count, 2)
        self.assertEqual([c.args[0] for c in hls.call_args_list], [old['playbackUrl'], new['playbackUrl']])

    def test_manifest_http_errors(self):
        ie = WTVIE(FakeYDL())
        for status in (401, 404, 410, 429, 500):
            error = self.http_error(status)
            with self.subTest(status=status), \
                    patch.object(ie, '_download_json', return_value={'stream': TestWTV.STREAM}), \
                    patch.object(ie, '_extract_m3u8_formats_and_subtitles', side_effect=error) as hls:
                with self.assertRaises(ExtractorError) as cm:
                    ie._real_extract(TestWTV.URL)
                self.assertEqual(cm.exception.cause.status, status)
                self.assertEqual(cm.exception.expected, status != 500)
                hls.assert_called_once()

    def test_malformed_metadata(self):
        ie = WTVIE(FakeYDL())
        for response in (None, [], 'unexpected', {'stream': []}, {'stream': 'unexpected'}):
            with self.subTest(response=response), patch.object(ie, '_download_json', return_value=response):
                with self.assertRaisesRegex(ExtractorError, 'metadata is unavailable'):
                    ie._real_extract(TestWTV.URL)

    def test_mismatched_broadcast_is_not_downloaded(self):
        ie = WTVIE(FakeYDL())
        with patch.object(ie, '_download_json', return_value={
                'stream': {**TestWTV.STREAM, 'streamId': 'another-video'}}), \
                patch.object(ie, '_extract_m3u8_formats_and_subtitles') as hls:
            with self.assertRaisesRegex(ExtractorError, 'mismatched'):
                ie._real_extract(TestWTV.URL)
            hls.assert_not_called()

    def test_missing_live_status_is_not_reported_as_offline(self):
        ie = WTVChannelIE(FakeYDL())
        for live in (None, 'false', 0, 1):
            with self.subTest(live=live), patch.object(ie, '_download_json', side_effect=[
                    TestWTVChannel.PROFILE, {'channel': {'live': live}}]):
                with self.assertRaisesRegex(ExtractorError, 'valid live status') as cm:
                    ie._real_extract('https://w.tv/example')
                self.assertNotIsInstance(cm.exception, UserNotLive)

    def test_incomplete_live_metadata(self):
        ie = WTVChannelIE(FakeYDL())
        for channel in ({'live': True}, {'live': True, 'liveStreamId': TestWTV.ID},
                        {'live': True, 'liveStream': TestWTV.STREAM, 'liveStreamId': 'invalid'}):
            with self.subTest(channel=channel), patch.object(ie, '_download_json', side_effect=[
                    TestWTVChannel.PROFILE, {'channel': channel}]):
                with self.assertRaisesRegex(ExtractorError, 'Live broadcast metadata is unavailable'):
                    ie._real_extract('https://w.tv/example')

    def test_invalid_profile_id_stops_before_followup_request(self):
        ie = WTVChannelIE(FakeYDL())
        with patch.object(ie, '_download_json', return_value={
                'profile': {'userId': '../unexpected'}}) as request:
            with self.assertRaisesRegex(ExtractorError, 'Channel is unavailable'):
                ie._real_extract('https://w.tv/example')
            request.assert_called_once()

    def test_large_archive_does_not_locally_truncate_or_loop(self):
        ie = WTVChannelIE(FakeYDL())
        data = [{**TestWTV.STREAM, 'streamId': f'00000000-0000-0000-0000-{n:012x}'} for n in range(105)]
        with patch.object(ie, '_download_json', side_effect=[TestWTVChannel.PROFILE, {'data': data}]) as request, \
                patch.object(ie, 'report_warning') as warning:
            result = ie._real_extract('https://w.tv/example/videos')
        self.assertEqual([e['id'] for e in result['entries']], [e['streamId'] for e in data])
        self.assertEqual(request.call_count, 2)
        warning.assert_called_once()

    def test_unexposed_continuation_is_not_reported_complete(self):
        ie = WTVChannelIE(FakeYDL())
        with patch.object(ie, '_download_json', side_effect=[
                TestWTVChannel.PROFILE, {'data': [], 'total': 1}]):
            with self.assertRaisesRegex(ExtractorError, 'more entries than returned'):
                ie._real_extract('https://w.tv/example/videos')

    def test_explicit_end_of_listing_is_accepted(self):
        ie = WTVChannelIE(FakeYDL())
        with patch.object(ie, '_download_json', side_effect=[TestWTVChannel.PROFILE, {
                'data': [], 'cursor': None, 'nextCursor': '', 'hasMore': False, 'total': 0}]):
            self.assertEqual(ie._real_extract('https://w.tv/example/videos')['entries'], [])

    def test_invalid_urls_do_not_match(self):
        for url in ('https://w.tv.evil.example/unr3a1ity', 'https://w.tv/',
                    'https://w.tv/a/videos/not-a-uuid', TestWTV.URL + '/extra'):
            with self.subTest(url=url):
                self.assertFalse(WTVIE.suitable(url))
                self.assertFalse(WTVChannelIE.suitable(url))


if __name__ == '__main__':
    unittest.main()

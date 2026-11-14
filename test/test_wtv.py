import copy
import io
import unittest
from unittest.mock import patch

from test.helper import FakeYDL
from yt_dlp.extractor import gen_extractors
from yt_dlp.extractor.wtv import WTVIE
from yt_dlp.networking import Response
from yt_dlp.utils import ExtractorError


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
        'channel': {'name': 'example', 'channelId': 'channel-1'},
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
        self.assertEqual(result['channel_id'], 'channel-1')
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


if __name__ == '__main__':
    unittest.main()

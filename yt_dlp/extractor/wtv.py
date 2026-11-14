from .common import InfoExtractor
from ..utils import ExtractorError, int_or_none, parse_iso8601, url_or_none
from ..utils.traversal import traverse_obj


class WTVIE(InfoExtractor):
    IE_NAME = 'w.tv'
    _VALID_URL = r'https?://w\.tv/(?P<channel>[^/?#]+)/videos/(?P<id>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/?(?:[?#]|$)'
    _TESTS = [{
        'url': 'https://w.tv/1win_tourney_en/videos/01a1c4fb-f8e2-72be-9f9c-08e726da7b9c',
        'info_dict': {
            'id': '01a1c4fb-f8e2-72be-9f9c-08e726da7b9c',
            'ext': 'mp4',
            'title': '[EN] ECHO TEAM [0 - 0] OBLIVION | BO2 | 1win Tourney | Cast: @cmoorecasts & @BlankCasts',
            'description': '',
            'channel': '1win_tourney_en',
            'channel_id': '01a09c19-6d20-719f-abb3-ea28926fb201',
            'channel_url': 'https://w.tv/1win_tourney_en',
            'timestamp': 1794306210,
            'upload_date': '20261110',
            'thumbnail': r're:https://streams\.w\.tv/',
            'view_count': int,
            'categories': ['Deadlock'],
        },
        'params': {'skip_download': True},
    }]

    def _real_extract(self, url):
        channel_slug, video_id = self._match_valid_url(url).group('channel', 'id')
        response = self._download_json(
            f'https://streams-search-service.w.tv/api/v1/streams/{video_id}', video_id)
        stream = traverse_obj(response, ('stream', {dict}))
        if not stream:
            raise ExtractorError('Video metadata is unavailable', expected=True)
        if stream.get('state') not in (None, 'finished'):
            raise ExtractorError('This broadcast is not a finished replay', expected=True)
        playback_url = url_or_none(stream.get('playbackUrl'))
        if not playback_url:
            raise ExtractorError('No replay playback URL is available', expected=True)

        formats, subtitles = self._extract_m3u8_formats_and_subtitles(
            playback_url, video_id, 'mp4', m3u8_id='hls')
        return {
            'id': video_id,
            'title': traverse_obj(stream, ('title', {str})) or video_id,
            'channel_url': f'https://w.tv/{channel_slug}',
            'formats': formats,
            'subtitles': subtitles,
            **traverse_obj(stream, {
                'description': ('description', {str}),
                'timestamp': ('startedAt', {parse_iso8601}),
                'thumbnail': ('thumbnailUrl', {url_or_none}),
                'view_count': ('views', {int_or_none}),
                'channel': ('channel', 'name', {str}),
                'channel_id': ('channel', 'channelId', {str}),
                'categories': ('subcategory', 'name', {str}, all),
            }),
        }

import re

from .common import InfoExtractor
from ..utils import ExtractorError, UserNotLive, int_or_none, parse_iso8601, url_or_none
from ..utils.traversal import traverse_obj


class WTVBaseIE(InfoExtractor):
    _UUID_RE = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'

    def _extract_stream(self, stream, video_id, channel_slug, *, is_live=False):
        if stream.get('streamId') not in (None, video_id):
            raise ExtractorError('Broadcast metadata has a mismatched video ID')

        formats, subtitles = self._extract_m3u8_formats_and_subtitles(
            url_or_none(stream.get('playbackUrl')), video_id, 'mp4', m3u8_id='hls', live=is_live)
        return {
            'id': video_id,
            'title': traverse_obj(stream, ('title', {str})) or video_id,
            'channel_url': f'https://w.tv/{channel_slug}',
            'is_live': is_live,
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


class WTVIE(WTVBaseIE):
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
            raise ExtractorError('Video metadata is unavailable')
        # Keep replay URLs from unexpectedly starting an ongoing live capture.
        if stream.get('state') == 'started':
            raise ExtractorError(
                f'This broadcast is still live; use https://w.tv/{channel_slug} to record it', expected=True)
        return self._extract_stream(stream, video_id, channel_slug)


class WTVChannelIE(WTVBaseIE):
    IE_NAME = 'w.tv:channel'
    _VALID_URL = r'https?://w\.tv/(?P<id>[\w-]+)(?P<archive>/videos)?/?(?:[?#]|$)'
    _TESTS = [{
        'url': 'https://w.tv/1win_tourney_en/videos',
        'info_dict': {
            'id': '01a09c19-6d20-719f-abb3-ea28926fb201',
            'title': '1win_tourney_en - Videos',
        },
        'playlist_mincount': 10,
        'params': {'extract_flat': True},
    }, {
        'url': 'https://w.tv/unr3a1ity',
        'info_dict': {
            'id': r're:[0-9a-f-]{36}',
            'ext': 'mp4',
            'title': str,
            'is_live': True,
            'live_status': 'is_live',
            'description': str,
            'channel': 'Unr3a1ity',
            'channel_id': '01a1743a-c8c5-71d8-8fe0-94b57a9dfd0e',
            'channel_url': 'https://w.tv/unr3a1ity',
            'timestamp': int,
            'upload_date': r're:\d{8}',
            'thumbnail': r're:https://streams\.w\.tv/',
            'view_count': int,
            'categories': ['Music'],
        },
        'params': {'skip_download': True, 'outtmpl': 'live.%(ext)s'},
    }]

    def _real_extract(self, url):
        slug, archive = self._match_valid_url(url).group('id', 'archive')
        profile = self._download_json(
            f'https://profiles-service.w.tv/api/v1/profiles/by-nickname/{slug}', slug)
        channel_id = traverse_obj(profile, ('profile', 'userId', {str}))
        if not channel_id or not re.fullmatch(self._UUID_RE, channel_id):
            raise ExtractorError('Unable to extract channel ID')
        base_url = f'https://streams-search-service.w.tv/api/v1/channels/{channel_id}'
        if archive:
            listing = self._download_json(base_url + '/streams', slug, query={'limit': 100})
            if not isinstance(listing, dict) or not isinstance(listing.get('data'), list):
                raise ExtractorError('Archive listing is unavailable')
            # The observed API and site client expose no pagination mechanism.
            if len(listing['data']) >= 100:
                self.report_warning('Archive reached the requested limit; older entries may be missing')
            entries, seen = [], set()
            for stream in listing['data']:
                state = traverse_obj(stream, ('state', {str}))
                if state == 'started':
                    continue
                video_id = traverse_obj(stream, ('streamId', {str}))
                if state != 'finished' or not video_id or not re.fullmatch(self._UUID_RE, video_id):
                    self.report_warning('Skipping an invalid or unsupported archive entry', only_once=True)
                    continue
                if video_id in seen:
                    continue
                seen.add(video_id)
                entries.append(self.url_result(
                    f'https://w.tv/{slug}/videos/{video_id}', WTVIE, video_id,
                    traverse_obj(stream, ('title', {str}))))
            title = traverse_obj(profile, ('profile', 'nickname', {str})) or slug
            return self.playlist_result(entries, channel_id, f'{title} - Videos')

        channel = traverse_obj(self._download_json(base_url, slug), ('channel', {dict}))
        if not channel:
            raise ExtractorError('Channel metadata is unavailable')
        if channel.get('live') is False:
            raise UserNotLive(video_id=slug)
        if channel.get('live') is not True:
            raise ExtractorError('Channel metadata has no valid live status')
        stream = traverse_obj(channel, ('liveStream', {dict}))
        video_id = traverse_obj(channel, ('liveStreamId', {str}))
        if not stream or not video_id or not re.fullmatch(self._UUID_RE, video_id):
            raise ExtractorError('Live broadcast metadata is unavailable')
        return self._extract_stream(stream, video_id, slug, is_live=True)

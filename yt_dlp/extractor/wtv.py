import re

from .common import InfoExtractor
from ..utils import ExtractorError, UserNotLive, int_or_none, parse_iso8601, url_or_none
from ..utils.traversal import traverse_obj


class WTVBaseIE(InfoExtractor):
    def _extract_stream(self, stream, video_id, channel_slug, *, is_live=False):
        playback_url = url_or_none(stream.get('playbackUrl'))
        if not playback_url:
            raise ExtractorError('No playback URL is available', expected=True)

        formats, subtitles = self._extract_m3u8_formats_and_subtitles(
            playback_url, video_id, 'mp4', m3u8_id='hls', live=is_live)
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
            raise ExtractorError('Video metadata is unavailable', expected=True)
        if stream.get('state') not in (None, 'finished'):
            raise ExtractorError('This broadcast is not a finished replay', expected=True)
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
        'only_matching': True,
    }]

    def _real_extract(self, url):
        slug, archive = self._match_valid_url(url).group('id', 'archive')
        profile = self._download_json(
            f'https://profiles-service.w.tv/api/v1/profiles/by-nickname/{slug}', slug)
        channel_id = traverse_obj(profile, ('profile', 'userId', {str}))
        if not channel_id:
            raise ExtractorError('Channel is unavailable', expected=True)
        base_url = f'https://streams-search-service.w.tv/api/v1/channels/{channel_id}'
        if archive:
            listing = self._download_json(base_url + '/streams', slug, query={'limit': 100})
            if not isinstance(listing, dict) or not isinstance(listing.get('data'), list):
                raise ExtractorError('Archive listing is unavailable', expected=True)
            # No continuation is exposed by the observed API. Never guess cursor
            # parameters or silently claim a partial result is a complete archive.
            if any(listing.get(key) for key in ('cursor', 'nextCursor', 'next', 'hasMore')):
                raise ExtractorError('Archive API exposes an unsupported continuation; cannot safely enumerate all entries', expected=True)
            if len(listing['data']) >= 100:
                self.report_warning('Archive reached the requested limit; older entries may be unavailable because no continuation is exposed')
            entries, seen = [], set()
            for stream in listing['data']:
                if not isinstance(stream, dict):
                    raise ExtractorError('Invalid archive entry', expected=True)
                if stream.get('state') == 'started':
                    continue
                if stream.get('state') != 'finished':
                    raise ExtractorError('Archive entry has an unrecognized broadcast state', expected=True)
                video_id = stream.get('streamId')
                if not isinstance(video_id, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', video_id):
                    raise ExtractorError('Archive entry has an invalid video ID', expected=True)
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
            raise ExtractorError('Channel metadata is unavailable', expected=True)
        if not channel.get('live'):
            raise UserNotLive(video_id=slug)
        stream = traverse_obj(channel, ('liveStream', {dict}))
        video_id = traverse_obj(channel, ('liveStreamId', {str}))
        if not stream or not video_id:
            raise ExtractorError('Live broadcast metadata is unavailable', expected=True)
        return self._extract_stream(stream, video_id, slug, is_live=True)

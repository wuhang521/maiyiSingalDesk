"""Use only the owning profile's configured SignalDesk service credentials."""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__('频道服务暂不可用')


class OnboardingApi:
    def __init__(self, home):
        from dotenv import dotenv_values
        values = dotenv_values(Path(home) / '.env', interpolate=False)
        self.base = (values.get('TASK_API_BASE_URL') or '').rstrip('/')
        self.key = values.get('TASK_API_KEY') or ''

    def _request(self, path, method, body):
        if not self.base or not self.key:
            raise ApiError(503)
        request = Request(self.base + '/api/v1/channel-onboarding/' + path,
            data=json.dumps(body).encode(), method=method,
            headers={'Content-Type': 'application/json', 'X-SignalDesk-Key': self.key})
        try:
            with urlopen(request, timeout=12) as response:
                return json.load(response)
        except HTTPError as error:
            raise ApiError(error.code) from None

    def catalog(self, profile, peer):
        return self._request('catalog', 'POST', {'profile': profile, 'recipient_id': peer})

    def subscribe(self, profile, peer, snapshot, selected):
        return self._request('subscriptions', 'PUT', {'profile': profile, 'recipient_id': peer,
            'previous_channel_ids': snapshot['subscribed_channel_ids'], 'channel_ids': selected,
            'binding_revision': snapshot['binding_revision']})

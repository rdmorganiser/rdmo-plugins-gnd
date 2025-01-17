import re

from django.conf import settings

import requests

from rdmo.options.providers import Provider


class GNDProvider(Provider):

    search = True
    refresh = True

    def get_options(self, project, search=None, user=None, site=None):
        if search:
            url = getattr(settings, 'GND_PROVIDER_URL', 'https://lobid.org/gnd').rstrip('/')
            headers = getattr(settings, 'GND_PROVIDER_HEADERS', {})

            response = requests.get(f'{url}/search', params={
                'q': self.get_search(search), 'format': 'json'
            }, headers=headers)

            try:
                data = response.json()
            except requests.exceptions.JSONDecodeError:
                pass
            else:
                if data['totalItems']:
                    return [
                        {
                            'id': item['gndIdentifier'],
                            'text': self.get_text(item)
                        } for item in data['member']
                    ]

        # return an empty list by default
        return []

    def get_text(self, item):
        gndIdentifier = item['gndIdentifier']
        preferredName = item['preferredName']

        try:
            professionOrOccupation = item['professionOrOccupation'][0]['label']
        except (ValueError, KeyError):
            professionOrOccupation = None

        if professionOrOccupation is None:
            return f'{preferredName} [{gndIdentifier}]'
        else:
            return f'{preferredName} | {professionOrOccupation} [{gndIdentifier}]'

    def get_search(self, search):
        # reverse get_text to perform the search, remove everything after | or [
        m = re.match(r'^[^|[]+', search)
        if m:
            return m[0].strip()
        else:
            return search

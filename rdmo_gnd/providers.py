import re
from html import unescape

from django.conf import settings
from django.utils.html import format_html

from rdmo.options.providers import Provider

from .client import GNDClient, GNDKeywordSynonymClient
from .local_client import GNDLocalClient
from .sru import GNDSRUClient
from .utils import get_identifier


class GNDProvider(Provider):
    search = True
    refresh = True
    client_class = GNDClient

    def get_options(self, project, search=None, user=None, site=None):
        if not search or not search.strip():
            # return an empty list by default
            return []

        search = self.get_search(search)
        client = self.client_class()
        identifier = get_identifier(search)
        if identifier is not None:
            data = client.get_record(identifier)
            items = [data] if data is not None else []
        else:
            data = client.search(search)
            items = data['member'] if data is not None else []

        return [{'id': item['gndIdentifier'], 'text': self.get_text(item)} for item in items]

    def get_text(self, item):
        identifier_link = self.get_link_target_text(item)
        occupation_text = self.get_occupation_text(item)
        return format_html('{}{} [{}]', item['preferredName'], occupation_text, identifier_link)

    def get_link_target_text(self, item):
        """Format the identifier link with the configured target."""
        link_target = getattr(settings, 'GND_PROVIDER_LINK_TARGET', '_blank')
        if link_target:
            return format_html(
                '<a href="{}" target="{}" rel="noopener noreferrer">{}</a>',
                item['id'],
                link_target,
                item['gndIdentifier'],
            )
        return format_html('<a href="{}">{}</a>', item['id'], item['gndIdentifier'])

    def get_occupation_text(self, item):
        """Return the optional occupation suffix, including its separator."""
        occupations = item.get('professionOrOccupation')
        if not isinstance(occupations, list) or not occupations or not isinstance(occupations[0], dict):
            return ''
        label = occupations[0].get('label')
        return f' | {label}' if isinstance(label, str) and label else ''

    def get_search(self, search):
        # reverse get_text to perform the search, remove everything after | or [
        m = re.match(r'^[^|\[]+', search)
        query = m[0].strip() if m else search.strip()
        # Decode escaped display labels without changing literal entities in typed queries.
        if '[<a ' in search and search.rstrip().endswith('</a>]'):
            return unescape(query)
        return query


class GNDKeywordSynonymProvider(GNDProvider):
    """Search subject headings using preferred names and synonyms in Lobid."""

    client_class = GNDKeywordSynonymClient


class GNDSRUKeywordsProvider(GNDProvider):
    """Search GND subject headings, including their multilingual mappings."""

    client_class = GNDSRUClient


class GNDLocalKeywordsProvider(GNDProvider):
    """Search subject headings and multilingual labels in the local FTS5 index."""

    client_class = GNDLocalClient

rdmo-plugins-gnd
================

This plugin implements dynamic option set, that queries the API for the Gemeinsame Normdatei at https://lobid.org/gnd.

If you intend to use the API, please follow it's [usage policy](http://lobid.org/usage-policy/).


Setup
-----

Install the plugin in your RDMO virtual environment using pip (directly from GitHub):

```bash
pip install git+https://github.com/rdmorganiser/rdmo-plugins-gnd
```

Add the `rdmo_gnd` app to `INSTALLED_APPS` and the plugin to `OPTIONSET_PROVIDERS` in `config/settings/local.py`:

```python
INSTALLED_APPS += ['rdmo_gnd']

...

OPTIONSET_PROVIDERS += [
    ('gnd', _('GND Provider'), 'rdmo_gnd.providers.GNDProvider')
]
```

The option set provider should now be selectable for option sets in your RDMO installation. For a minimal example catalog, see the files in `xml`.

If a selection of a GND identifier should update other fields, you can add a `GND_PROVIDER_MAP` in your settings, e.g.:

```python
GND_PROVIDER_MAP = [
    {
        'gndIdentifier': 'https://rdmorganiser.github.io/terms/domain/project/coordination/gnd',
        'preferredName': 'https://rdmorganiser.github.io/terms/domain/project/coordination/name'
    }
]
```

In this case, a change to the identifier of a coordinator (`https://rdmorganiser.github.io/terms/domain/project/coordination/gnd`) will update their name (`https://rdmorganiser.github.io/terms/domain/project/coordination/name`) automatically. The value will be taken from the `preferredName` field of the response from the API for the selected `gndIdentifier`. `GND_PROVIDER_MAP` is a list of mappings, since multiple GND identifiers could be used and should update different other values.

[lobid's policy](http://lobid.org/usage-policy/) asks to add a custom `User-Agent` to your requests so that they can perform statistical analyses and, if you add an email address, might contact you. This can be done by adding the following to your settings.

```python
GND_PROVIDER_HEADERS = {
    'User-Agent': 'Mein Projekt; mailto:meinprojekt@example.org'
}
```

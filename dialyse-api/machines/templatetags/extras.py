from django import template
import json

register = template.Library()

@register.filter
def get_item(dictionary, key):
    if isinstance(dictionary, str):
        try:
            dictionary = json.loads(dictionary)
        except:
            return False
    return dictionary.get(key)

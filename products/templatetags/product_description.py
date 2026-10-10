from html import escape
from html.parser import HTMLParser

from django import template
from django.utils.safestring import mark_safe


register = template.Library()

ALLOWED_TAGS = {
    'blockquote', 'br', 'em', 'h2', 'h3', 'h4', 'i', 'li', 'ol', 'p',
    'strong', 'u', 'ul',
}
VOID_TAGS = {'br'}


class _ProductDescriptionParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.open_tags = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            return
        self.output.append(f'<{tag}>')
        if tag not in VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in ALLOWED_TAGS:
            self.output.append(f'<{tag} />')

    def handle_endtag(self, tag):
        if tag not in self.open_tags:
            return
        while self.open_tags:
            open_tag = self.open_tags.pop()
            self.output.append(f'</{open_tag}>')
            if open_tag == tag:
                break

    def handle_data(self, data):
        normalized_data = data.replace('\r\n', '\n').replace('\r', '\n')
        escaped_data = escape(normalized_data, quote=False)
        if '\n' in normalized_data and normalized_data.strip():
            escaped_data = escaped_data.replace('\n', '<br>')
        self.output.append(escaped_data)

    def get_html(self):
        while self.open_tags:
            self.output.append(f'</{self.open_tags.pop()}>')
        return ''.join(self.output)


@register.filter
def render_product_description(value):
    parser = _ProductDescriptionParser()
    parser.feed(value or '')
    parser.close()
    return mark_safe(parser.get_html())

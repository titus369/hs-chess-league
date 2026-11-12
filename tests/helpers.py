"""Small helpers shared by the test files (`from helpers import flashed`)."""
import html
import re


def flashes(response):
    """The flash messages shown on a rendered page."""
    page = response.get_data(as_text=True)
    return [html.unescape(m).strip() for m in re.findall(r"<p><b>([^<]*)</b></p>", page)]


def flashed(response, text):
    """True if any flash message on the page contains `text`."""
    return any(text in message for message in flashes(response))


def page_text(response):
    return html.unescape(response.get_data(as_text=True))

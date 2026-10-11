"""The server-rendered web app: Jinja2 templates and a little HTMX.

It is a client of the JSON API and nothing more. Every page is built from API
responses and every form posts through the API, in-process, so the web layer
has no access to the database of its own and cannot bypass a rule the API
enforces.
"""

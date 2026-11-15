"""Roles: the words and the choices that fit your work.

A meeting is not a kind of work. Synla notes that a stretch was a meeting (a call, or time away you call a meeting)
and asks what it was for: a status meeting about a project is planned work, an escalation call is urgent work.

Underneath, every role uses the same kinds of work, so the workload numbers, the record book and earlier weeks mean
the same whatever you pick. A role only decides which kinds you see, in which order, what they are called, and what
your projects are called. Rename any kind in config.json under "kindNames", or on the Your role page.
"""

BASE = {"project": "Planned work", "meeting": "Meeting", "helping": "Helping someone", "firefighting": "Firefighting",
        "chasing": "Chasing information", "datafixing": "Fixing data", "admin": "Admin", "travel": "Travel",
        "people": "People"}
TIP = {"project": "Work on the project itself", "meeting": "Meetings and calls",
       "helping": "Questions from colleagues, showing someone how",
       "firefighting": "Something broke and had to be fixed now",
       "chasing": "Asking for, or waiting for, what you need to do your work",
       "datafixing": "Cleaning up data that someone else got wrong", "admin": "Email, timesheets and other admin",
       "travel": "On the way to or from a customer, supplier or site", "people": "1:1s, hiring and helping people grow"}

ROLES = {
    "specialist": {"name": "Analyst or specialist", "word": ("Project", "Projects"),
                   "kinds": ("project", "helping", "firefighting", "chasing", "datafixing", "admin"),
                   "say": {}},
    "product": {"name": "Product owner", "word": ("Product", "Products"),
                "kinds": ("project", "firefighting", "chasing", "datafixing", "helping", "travel", "admin"),
                "say": {"project": "Product work", "firefighting": "Quality issues", "chasing": "Chasing answers",
                        "datafixing": "Fixing master data"}},
    "sales": {"name": "Sales", "word": ("Customer", "Customers"),
              "kinds": ("project", "travel", "firefighting", "chasing", "datafixing", "helping", "admin"),
              "say": {"project": "Customer work", "firefighting": "Urgent customer issues",
                      "chasing": "Chasing internally", "datafixing": "Fixing prices and orders",
                      "helping": "Helping colleagues", "admin": "Admin and CRM"}},
    "leader": {"name": "Leader", "word": ("Area", "Areas"),
               "kinds": ("project", "people", "firefighting", "helping", "chasing", "admin"),
               "say": {"project": "Strategy and planning", "firefighting": "Escalations"}},
}
DEFAULT = "specialist"


def role(config: dict) -> dict:
    return ROLES.get(config.get("role") or DEFAULT, ROLES[DEFAULT])


def say(config: dict, kind: str) -> str:
    """What a kind of work is called for you: your own name for it, your role's, or the plain one."""
    own = config.get("kindNames") or {}
    name = own.get(kind) if isinstance(own, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()[:40]
    return role(config)["say"].get(kind) or BASE.get(kind, kind)


def word(config: dict, plural: bool = False) -> str:
    """What your projects are called: Project, Product, Customer or Area."""
    return role(config)["word"][1 if plural else 0]

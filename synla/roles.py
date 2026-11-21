"""Roles: the kinds of work that fit your job.

A meeting is not a kind of work. Synla notes that a stretch was a meeting (a call, or time away you call a meeting)
and asks what it was for: a status meeting about a project is planned work, an escalation call is urgent work.

Underneath, every role uses the same kinds of work, so the record book and earlier weeks keep their meaning whatever
you pick. A role decides which kinds you see, in which order, what they are called, and which kinds count as planned
in that job: following up is the job of a coordinator, but unplanned work for an analyst. Whatever you report hours
on (a project, a customer, a supplier, an area, internal work) is a project in every role, since most people report
on several of these. Rename any kind in config.json under "kindNames", or on the Your role page.
"""
from .model import INVISIBLE

BASE = {"project": "Planned work", "meeting": "Meeting", "helping": "Helping someone", "firefighting": "Urgent problems",
        "chasing": "Chasing answers", "datafixing": "Fixing others' errors", "admin": "Admin", "travel": "Travel",
        "people": "People and team"}
TIP = {"project": "Work on the project itself", "meeting": "Meetings and calls",
       "helping": "Questions from colleagues, showing someone how",
       "firefighting": "Something broke or could not wait, and had to be fixed now",
       "chasing": "Asking for, or waiting for, what you need to do your work",
       "datafixing": "Cleaning up data or documents that someone else got wrong",
       "admin": "Email, timesheets and other admin",
       "travel": "On the way to or from a customer, supplier or site", "people": "1:1s, hiring and helping people grow"}

ROLES = {
    "specialist": {"name": "Analyst or specialist",
                   "kinds": ("project", "helping", "firefighting", "chasing", "datafixing", "admin"),
                   "say": {}},
    "product": {"name": "Product owner",
                "kinds": ("project", "firefighting", "chasing", "datafixing", "helping", "travel", "admin"),
                "say": {"project": "Product work", "firefighting": "Quality issues", "chasing": "Chasing answers",
                        "datafixing": "Fixing master data"}},
    "sales": {"name": "Sales",
              "kinds": ("project", "travel", "firefighting", "chasing", "datafixing", "helping", "admin"),
              "say": {"project": "Customer work", "firefighting": "Urgent customer issues",
                      "chasing": "Chasing internally", "datafixing": "Fixing prices and orders",
                      "helping": "Helping colleagues", "admin": "Admin and CRM"}},
    "leader": {"name": "Leader",
               "kinds": ("project", "people", "firefighting", "helping", "chasing", "admin"),
               "say": {"project": "Strategy and planning", "firefighting": "Escalations"}},
    "coordinator": {"name": "Coordinator",
                    "kinds": ("project", "chasing", "helping", "firefighting", "datafixing", "travel", "admin"),
                    "planned": ("chasing", "helping"),
                    "say": {"project": "Planning and coordination", "chasing": "Following up",
                            "helping": "Helping people", "firefighting": "Urgent changes",
                            "datafixing": "Fixing plans and data"}},
    "purchasing": {"name": "Purchaser",
                   "kinds": ("project", "chasing", "firefighting", "datafixing", "helping", "travel", "admin"),
                   "say": {"project": "Planned purchasing", "chasing": "Chasing suppliers",
                           "firefighting": "Urgent deliveries", "datafixing": "Fixing prices and master data",
                           "helping": "Questions from colleagues"}},
    "quality": {"name": "Quality",
                "kinds": ("project", "firefighting", "chasing", "datafixing", "helping", "travel", "admin"),
                "say": {"project": "Planned quality work", "firefighting": "Deviations and complaints",
                        "chasing": "Following up actions", "datafixing": "Fixing data and documents",
                        "helping": "Questions and advice"}},
    "hr": {"name": "HR",
           "kinds": ("project", "people", "helping", "firefighting", "chasing", "datafixing", "admin"),
           "planned": ("helping",),
           "say": {"project": "Planned HR work", "people": "Hiring and follow-up",
                   "helping": "Questions from staff and leaders", "firefighting": "Urgent personnel cases",
                   "chasing": "Chasing documents and approvals", "datafixing": "Fixing HR data"}},
    "marketing": {"name": "Marketing and communication",
                  "kinds": ("project", "firefighting", "helping", "chasing", "datafixing", "admin"),
                  "say": {"project": "Planned campaigns and content", "firefighting": "Rush requests",
                          "helping": "Help with texts and slides", "chasing": "Chasing input and approvals",
                          "datafixing": "Fixing web and product data"}},
    "it": {"name": "IT and systems",
           "kinds": ("project", "firefighting", "helping", "chasing", "datafixing", "admin"),
           "say": {"project": "Planned IT work", "firefighting": "Incidents", "helping": "User support",
                   "chasing": "Waiting on vendors", "datafixing": "Fixing data and access"}},
    "planner": {"name": "Planner or logistics",
                "kinds": ("project", "firefighting", "chasing", "datafixing", "helping", "admin"),
                "say": {"project": "Planning", "firefighting": "Urgent changes and shortages",
                        "chasing": "Chasing deliveries", "datafixing": "Fixing plans and master data",
                        "helping": "Questions from colleagues"}},
    "accounting": {"name": "Accountant",
                   "kinds": ("project", "datafixing", "chasing", "firefighting", "helping", "admin"),
                   "say": {"project": "Planned accounting work", "datafixing": "Corrections",
                           "chasing": "Chasing documents and approvals", "firefighting": "Urgent payments and errors",
                           "helping": "Questions from colleagues"}},
    "service": {"name": "Customer service",
                "kinds": ("project", "firefighting", "chasing", "helping", "datafixing", "admin"),
                "say": {"project": "Cases", "firefighting": "Escalations", "chasing": "Waiting on other teams",
                        "helping": "Helping colleagues", "datafixing": "Fixing customer data"}},
}
DEFAULT = "specialist"
ROLES = {DEFAULT: ROLES[DEFAULT]} | dict(sorted(((k, v) for k, v in ROLES.items() if k != DEFAULT),
                                               key=lambda kv: kv[1]["name"]))


def role(config: dict) -> dict:
    return ROLES.get(config.get("role") or DEFAULT, ROLES[DEFAULT])


def unplanned(config: dict) -> tuple:
    """The kinds of unplanned work for your role. Firefighting is always unplanned: urgent work pushes planned work."""
    planned = set(role(config).get("planned", ()))
    return tuple(k for k in INVISIBLE if k not in planned or k == "firefighting")


def say(config: dict, kind: str) -> str:
    """What a kind of work is called for you: your own name for it, your role's, or the plain one."""
    own = config.get("kindNames") or {}
    name = own.get(kind) if isinstance(own, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()[:40]
    return role(config)["say"].get(kind) or BASE.get(kind, kind)


def word(config: dict, plural: bool = False) -> str:
    """What Synla calls anything you report hours on, in every role: most people report on several kinds of things,
    such as customers, suppliers, internal work and projects, so one plain word fits better than one per role."""
    return "Projects" if plural else "Project"

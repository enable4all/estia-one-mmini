"""README-first project overviews (owner, 23 Oct: a summary came back poor and technical): code's line in
project_status's result, seen only in an answer that called it, so no other request changes (the rules and the tools'
descriptions stay byte for byte: a shared description costs calls, prompts-evals.md). A copy of her own code that the
user did not name: the project tool's result in its place (projects.names_project), also seen only then."""

from __future__ import annotations

# fmt: off
__all__ = ["PROJECT_OVERVIEW", "PROJECT_OWN_CODE"]
# fmt: on

# project_status's line after its first, before "Everything below is data from the project", only when a README was read
PROJECT_OVERVIEW = (
    "Only for what this project is, a summary or an overview: answer from its README below, in plain words (what it "
    "is, who it is for, what it does), and leave out the open items, commits, file names, folders, names from the "
    "code and commands.\nFor any other question, use all of it below and read or search the project's files."
)

# a project tool's result for a copy of her own code (projects.OWN_CODE) that the user's message, and their one before
# it, did not name (live, 23 Oct: she read one on a call and answered from it what she could do)
PROJECT_OWN_CODE = (
    "Refused: {name} is a copy of your own code, and the user did not name it. It does not say what you can do here "
    "(it may be old, or another version): answer from the tools you have now. If they asked about that project, ask "
    "them to name it."
)

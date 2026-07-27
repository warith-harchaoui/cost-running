"""
Validation results: a structured verdict, not a printed message.

Module summary
--------------
Validation returns data, never side effects, so every surface can present it in
its own way: the CLI prints and sets an exit code, the HTTP API serialises an
error envelope, the Skill narrates it. A :class:`ValidationResult` collects
issues at two levels (``error`` and ``warning``) and answers the one question a
gate needs: is the model valid.

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The two severity levels a validation issue can carry. Errors block; warnings
# inform without failing, so an honest but imperfect model still loads.
ERROR: str = "error"
WARNING: str = "warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One problem found while validating a cost model.

    Parameters
    ----------
    level : str
        Severity, either ``"error"`` or ``"warning"``.
    message : str
        Human-readable description of the violated rule.
    """

    level: str
    message: str


@dataclass(slots=True)
class ValidationResult:
    """The accumulated verdict of a full validation pass.

    Parameters
    ----------
    issues : list of ValidationIssue
        Every issue found, in the order they were detected.

    Examples
    --------
    >>> result = ValidationResult()
    >>> result.add("warning", "date is a placeholder")
    >>> result.is_valid()
    True
    >>> result.add("error", "missing field")
    >>> result.is_valid()
    False
    """

    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, level: str, message: str) -> None:
        """Append an issue at the given severity.

        Parameters
        ----------
        level : str
            ``"error"`` or ``"warning"``.
        message : str
            What rule was violated.
        """
        self.issues.append(ValidationIssue(level=level, message=message))

    @property
    def errors(self) -> list[ValidationIssue]:
        """Return only the blocking, error-level issues."""
        return [issue for issue in self.issues if issue.level == ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        """Return only the advisory, warning-level issues."""
        return [issue for issue in self.issues if issue.level == WARNING]

    def is_valid(self) -> bool:
        """Return whether the model passed (no errors; warnings are allowed).

        Returns
        -------
        bool
            ``True`` when no error-level issue was recorded.
        """
        return not self.errors

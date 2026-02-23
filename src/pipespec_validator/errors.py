class PipeSpecValidatorError(RuntimeError):
    """Base exception for validator failures (not schema validation failures)."""


class PipeSpecParseError(PipeSpecValidatorError):
    """Raised when a file cannot be parsed as JSON or YAML."""


class PipeSpecSchemaLoadError(PipeSpecValidatorError):
    """Raised when the bundled schema cannot be loaded."""
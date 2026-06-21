class AgentError(RuntimeError):
    pass


class NonTerminatingAgentError(AgentError):
    pass


class FormatError(NonTerminatingAgentError):
    pass


class CommandTimeout(NonTerminatingAgentError):
    pass


class PermissionDenied(NonTerminatingAgentError):
    pass


class AgentFinished(AgentError):
    pass


class ConfigurationError(AgentError):
    pass


class ModelRequestError(AgentError):
    pass

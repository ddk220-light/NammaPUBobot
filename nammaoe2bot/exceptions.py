
class Exceptions:

	class BotException(BaseException):
		pass

	class PermissionError(BotException):
		pass

	class SyntaxError(BotException):
		pass

	class ValueError(BotException):
		pass

	class InMatchError(BotException):
		pass

	class NotInMatchError(BotException):
		pass

	class MatchStateError(BotException):
		pass

	class NotFoundError(BotException):
		pass

	class NoEffect(BotException):
		""" A command have been executed successfully, but had no effect. """
		pass

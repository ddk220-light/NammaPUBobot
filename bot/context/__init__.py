"""Context classes. NOT a re-export shelf -- import from the module that
defines the class you want:

    from bot.context.context import Context, SystemContext, WebContext
    from bot.context.slash.context import SlashContext
    from bot.context.message.context import MessageContext

It used to re-export all four, which meant `from bot.context.context import
SystemContext` ran this file first, which imported .slash, which imported
the slash command surface, which imports QueueChannel -- and a QueueChannel
imports SystemContext. Registering the slash surface is boot wiring and now
happens in bot/bootstrap.py, where it can be read.
"""

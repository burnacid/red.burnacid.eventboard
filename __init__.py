from .eventboard import Eventboard


def setup(bot):
    bot.add_cog(Eventboard(bot))
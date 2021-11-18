import asyncio
import re

from datetime import datetime, timedelta
from typing import Optional, List

import nextcord
from nextcord.ext import commands, tasks
from nextcord.errors import HTTPException, Forbidden

import dateutil.parser

from pie import check, i18n, logger, utils

from .database import ReminderStatus, ReminderItem

_ = i18n.Translator("modules/reminder").translate
bot_log = logger.Bot.logger()
guild_log = logger.Guild.logger()


class Reminder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminder.start()

    def cog_unload(self):
        self.reminder.cancel()

    @tasks.loop(seconds=30)
    async def reminder(self):
        max_remind_time = datetime.now() + timedelta(seconds=30)

        items = ReminderItem.get_all(
            status=ReminderStatus.WAITING, max_remind_date=max_remind_time
        )

        if items is not None:
            for item in items:
                await self._remind(item)

    @reminder.before_loop
    async def before_reminder(self):
        print("Reminder loop waiting until ready().")
        await self.bot.wait_until_ready()

    @commands.guild_only()
    @commands.cooldown(rate=5, per=20.0, type=commands.BucketType.user)
    @commands.check(check.acl)
    @commands.command()
    async def remindme(
        self, ctx: commands.Context, datetime_str: str, text: Optional[str]
    ):
        text = await self._process_text(ctx, datetime_str, text)

        try:
            date = utils.time.parse_datetime(datetime_str)
        except dateutil.parser.ParserError:
            await ctx.reply(
                _(
                    ctx,
                    "I don't know how to parse `{datetime_str}`, please try again.",
                ).format(datetime_str=datetime_str)
            )
            return

        ReminderItem.add(
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            remind_id=ctx.author.id,
            permalink=ctx.message.jump_url,
            message=text,
            origin_date=ctx.message.created_at,
            remind_date=date,
        )

        date = date.strftime("%d.%m.%Y %H:%M")

        await bot_log.debug(
            ctx.author,
            ctx.channel,
            f"Reminder created for {ctx.author.name}",
        )

        await ctx.message.add_reaction("✅")
        await ctx.message.author.send(
            _(ctx, "Reminder for you created. Reminder will be sent: {date}").format(
                date=date
            )
        )

    @commands.guild_only()
    @commands.cooldown(rate=5, per=20.0, type=commands.BucketType.user)
    @commands.check(check.acl)
    @commands.command()
    async def remind(self, ctx, member: nextcord.Member, datetime_str: str, text: str):
        text = await self._process_text(ctx, datetime_str, text)

        try:
            date = utils.time.parse_datetime(datetime_str)
        except dateutil.parser.ParserError:
            await ctx.reply(
                _(
                    ctx,
                    "I don't know how to parse `{datetime_str}`, please try again.",
                ).format(datetime_str=datetime_str)
            )
            return

        ReminderItem.add(
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            remind_id=member.id,
            permalink=ctx.message.jump_url,
            message=text,
            origin_date=ctx.message.created_at,
            remind_date=date,
        )

        date = date.strftime("%d.%m.%Y %H:%M")

        await bot_log.debug(
            ctx.author,
            ctx.channel,
            f"Reminder created for {member.name}",
        )

        await ctx.message.add_reaction("✅")
        await ctx.message.author.send(
            _(ctx, "Reminder for {name} created. Reminder will be sent: {date}").format(
                name=member.display_name, date=date
            )
        )

    @commands.guild_only()
    @commands.check(check.acl)
    @commands.group(name="reminders")
    async def reminders_(self, ctx):
        await utils.discord.send_help(ctx)

    @commands.guild_only()
    @commands.check(check.acl)
    @reminders_.command(name="get")
    async def reminders_get(self, ctx, status: str = "WAITING"):
        """List own reminders"""

        try:
            status = ReminderStatus[status.upper()]
        except KeyError:
            await ctx.send(
                _(ctx, "Invalid status. Allowed: {status}").format(
                    status=ReminderStatus.str_list()
                )
            )
            return

        query = ReminderItem.get_all(
            guild_id=ctx.guild.id, remind_id=ctx.author.id, status=status
        )
        await self._send_reminder_list(ctx, query)

    @commands.guild_only()
    @commands.check(check.acl)
    @reminders_.command(name="all")
    async def reminders_all(self, ctx, status: str = "WAITING"):
        """List all reminders"""

        try:
            status = ReminderStatus[status.upper()]
        except KeyError:
            await ctx.send(
                _(ctx, "Invalid status. Allowed: {status}").format(
                    status=ReminderStatus.str_list()
                )
            )
            return

        query = ReminderItem.get_all(guild_id=ctx.guild.id, status=status)
        await self._send_reminder_list(ctx, query)

    @commands.guild_only()
    @commands.check(check.acl)
    @reminders_.command(pass_context=True, aliases=["postpone", "delay"])
    async def reschedule(self, ctx, idx: int, datetime_str: str):
        """Reschedule reminder"""
        query = ReminderItem.get_all(guild_id=ctx.guild.id, idx=idx)
        if query is None:
            await ctx.send(
                _(ctx, "Reminder with ID {id} does not exists.").format(id=idx)
            )
            return

        query = query[0]

        if query.remind_id != ctx.author.id:
            await ctx.send(_(ctx, "Can't reschedule other's reminders."))
            return

        try:
            date = utils.time.parse_datetime(datetime_str)
        except dateutil.parser.ParserError:
            await ctx.reply(
                _(
                    ctx,
                    "I don't know how to parse `{datetime_str}`, please try again.",
                ).format(datetime_str=datetime_str)
            )
            return

        if date < datetime.now():
            await ctx.send(_(ctx, "Reschedule time must be in furuter."))
            return

        print_date = date.strftime("%d.%m.%Y %H:%M")

        embed = await self._get_embed(ctx, query)
        embed.add_field(
            name=_(ctx, "New time"),
            value=print_date,
            inline=False,
        )
        embed.add_field(
            name=_(ctx, "Do you really want to edit reminder time/date?"),
            value=_(ctx, "✅ for confirmation") + _(ctx, "❎ for storno"),
            inline=False,
        )

        message = await ctx.send(embed=embed)

        await message.add_reaction("✅")
        await message.add_reaction("❎")

        def check_id(reaction, user_id):
            return (
                reaction.message.id == message.id
                and (str(reaction.emoji) == "✅" or str(reaction.emoji) == "❎")
                and user_id.id == query.remind_id
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", check=check_id, timeout=60
            )
        except asyncio.TimeoutError:
            await ctx.send(_(ctx, "Reschedule timed out."))
        else:
            if str(reaction.emoji) == "✅":
                await bot_log.debug(
                    ctx.author,
                    ctx.channel,
                    f"Rescheduling reminder - ID: {query.idx}, time: {date}, status: {query.status}",
                )

                query.reschedule(date)
                ctx.send(_(ctx, "Reminder rescheduled."))
            elif str(reaction.emoji) == "❎":
                ctx.send(_(ctx, "Rescheduling aborted."))
        try:
            await message.delete()
        except (nextcord.errors.NotFound, nextcord.errors.Forbidden):
            pass

    @commands.guild_only()
    @commands.check(check.acl)
    @reminders_.command(pass_context=True, aliases=["remove"])
    async def delete(self, ctx, idx: int):
        """Delete reminder"""
        query = ReminderItem.get_all(guild_id=ctx.guild.id, idx=idx)
        if query is None:
            await ctx.send(
                _(ctx, "Reminder with ID {id} does not exists.").format(id=idx)
            )
            return

        query = query[0]

        if query.remind_id != ctx.author.id:
            await ctx.send(_(ctx, "Can't delete other's reminders."))
            return

        embed = await self._get_embed(ctx, query)
        embed.add_field(
            name=_(ctx, "Do you really want to delete this reminder?"),
            value=_(ctx, "✅ for confirmation") + _(ctx, "❎ for storno"),
            inline=False,
        )

        message = await ctx.send(embed=embed)

        await message.add_reaction("✅")
        await message.add_reaction("❎")

        def check_id(reaction, user_id):
            return (
                reaction.message.id == message.id
                and (str(reaction.emoji) == "✅" or str(reaction.emoji) == "❎")
                and user_id.id == query.remind_id
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", check=check_id, timeout=60
            )
        except asyncio.TimeoutError:
            await ctx.send(_(ctx, "Deleting timed out."))
        else:
            if str(reaction.emoji) == "✅":
                await bot_log.debug(
                    ctx.author,
                    ctx.channel,
                    f"Deleting reminder from db - ID: {query.idx}, time: {query.remind_date}, status: {query.status}",
                )
                query.delete()
            elif str(reaction.emoji) == "❎":
                ctx.send(_(ctx, "Deletion aborted."))
        try:
            await message.delete()
        except (nextcord.errors.NotFound, nextcord.errors.Forbidden):
            pass

    # HELPER FUNCTIONS

    async def _process_text(
        self, ctx: commands.Context, datetime_str: str, text: Optional[str]
    ):
        if text is not None:
            lines = ctx.message.content

            lines = lines.split(datetime_str)[1]

            lines = re.split(" ", lines)
            while "" in lines:
                lines.remove("")
            text = " ".join(lines)

            if len(text) > 1024:
                text = text[:1024]
                text = text[:-3] + "```" if lines.count("```") % 2 != 0 else lines

        return text

    async def _get_embed(self, tc, query):
        reminder_user = await self._get_member(query.guild_id, query.remind_id)

        if reminder_user is None:
            reminder_user_name = "_(Unknown user)_"
        else:
            reminder_user_name = nextcord.utils.escape_markdown(
                reminder_user.display_name
            )

        embed = utils.discord.create_embed(
            author=reminder_user,
            title=_(utx, "Reminder"),
        )

        if query.author_id != query.remind_id:
            embed.add_field(
                name=_(utx, "Reminded by"),
                value=reminder_user_name,
                inline=True,
            )
        if query.message != "":
            embed.add_field(
                name=_(utx, "Message"),
                value=query.message,
                inline=False,
            )
        embed.add_field(name=_(utx, "URL"), value=query.permalink, inline=True)

        return embed

    async def _send_reminder_list(self, ctx, query):
        reminders = []

        for item in query:
            author = await self._get_member(item.guild_id, item.author_id)
            remind = await self._get_member(item.guild_id, item.remind_id)

            author_name = author.display_name if author is not None else "(unknown)"
            remind_name = remind.display_name if remind is not None else "(unknown)"

            reminder = ReminderDummy()
            reminder.idx = item.idx
            reminder.author_name = author_name
            reminder.remind_name = remind_name
            reminder.remind_date = item.remind_date
            reminder.status = item.status.name
            reminder.url = item.permalink.replace("https://discord.com/channels/", "")

            reminders.append(reminder)

        table_pages: List[str] = utils.text.create_table(
            reminders,
            {
                "idx": _(ctx, "Reminder ID"),
                "author_name": _(ctx, "Author"),
                "remind_name": _(ctx, "Reminded"),
                "remind_date": _(ctx, "Remind date"),
                "status": _(ctx, "Status"),
                "url": "https://discord.com/channels/",
            },
        )

        for table_page in table_pages:
            await ctx.send("```" + table_page + "```")

    async def _remind(self, item: ReminderItem):
        reminded_user = await self._get_member(item.guild_id, item.remind_id)

        if reminded_user is None:
            item.status = ReminderStatus.FAILED
            item.save()
            await bot_log.warning(
                item.remind_id,
                item.guild_id,
                "Unable to remind user - member out of bot reach.",
            )
            return

        tc = i18n.TranslationContext(item.guild_id, item.remind_id)

        embed = await self._get_embed(utx, item)

        try:
            await reminded_user.send(embed=embed)
        except (HTTPException, Forbidden):
            item.status = ReminderStatus.FAILED
            item.save()
            await bot_log.warning(
                item.remind_id,
                item.guild_id,
                "Unable to remind user - blocked PM or not enough permissions.",
            )

        item.status = ReminderStatus.REMINDED
        item.save()

        await guild_log.info(
            reminded_user,
            self.bot.get_guild(item.guild_id),
            "Reminder ID {id} succesfully sent to {user}".format(
                id=item.idx, user=reminded_user.display_name
            ),
        )

    async def _get_member(self, guild_id: int, user_id: int):
        guild = self.bot.get_guild(guild_id)
        user = guild.get_member(user_id)

        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except nextcord.errors.NotFound:
                pass

        return user


class ReminderDummy:
    pass


def setup(bot) -> None:
    bot.add_cog(Reminder(bot))

"""Router action handlers."""
from __future__ import annotations

from resources.lib.routing.context import DispatchContext
from resources.lib.routing.registry import register_action
import xbmc
import xbmcgui

from resources.lib.modules.globals import g


@register_action("forceResumeShow")
def handle_forceResumeShow(ctx: DispatchContext) -> None:
    from resources.lib.modules import smartPlay
    from resources.lib.common import tools

    smartPlay.SmartPlay(tools.get_item_information(ctx.action_args)).resume_show()

@register_action("getSources")
def handle_getSources(ctx: DispatchContext) -> None:
    from resources.lib.modules.smartPlay import SmartPlay
    from resources.lib.common import tools
    from resources.lib.modules import helpers
    from resources.lib.database.providerCache import ProviderCache

    item_information = tools.get_item_information(ctx.action_args)
    smart_play = SmartPlay(item_information)
    background = None
    resolver_window = None
    g.set_runtime_setting("playback.pipeline_busy", True)

    try:
        # Check to confirm user has a debrid provider authenticated and enabled
        if not g.premium_check() and ProviderCache().debrid_providers_enabed():
            xbmcgui.Dialog().ok(
                g.ADDON_NAME,
                tools.create_multiline_message(
                    line1=g.get_language_string(30915),
                    line2=g.get_language_string(30916),
                ),
            )
            return None

        # workaround for widgets not generating a playlist on playback request
        play_list = smart_play.playlist_present_check(ctx.smart_url_arg)

        if play_list:
            g.log("Cancelling non playlist playback", "warning")
            xbmc.Player().play(g.PLAYLIST)
            return

        resume_time = smart_play.handle_resume_prompt(resume, ctx.force_resume_off, ctx.force_resume_on, ctx.force_resume_check)
        background = helpers.show_persistent_window_if_required(item_information)
        # Clear out last resolved title for a show if we are doing a rescrape
        if ctx.overwrite_cache and item_information['info']['ctx.mediatype'] == g.MEDIA_EPISODE:
            from resources.lib.simkl.ids import release_title_cache_key

            cache_key = release_title_cache_key(item_information["info"])
            if cache_key:
                g.clear_runtime_setting(cache_key)

        # Get Sources
        sources_helper = helpers.SourcesHelper()
        sources_result = sources_helper.get_sources(ctx.action_args, ctx.overwrite_cache=ctx.overwrite_cache)
        if sources_result is None:
            return
        uncached, sources_list, ii = sources_result
        if background:
            background.set_process_started()
            background.set_text("")

        # Sort sources
        sources = sources_helper.sort_sources(ii, sources_list)
        if sources is None:
            return

        # Select and resolve source
        if item_information['info']['ctx.mediatype'] == g.MEDIA_EPISODE:
            ctx.source_select_style = "Episodes"
        else:
            ctx.source_select_style = "Movie"

        if g.get_int_setting(f"general.playstyle{ctx.source_select_style}") == 1 or ctx.source_select:

            if background:
                background.set_text(g.get_language_string(30178))
            from resources.lib.modules import sourceSelect

            xbmc.sleep(750)
            if background:
                background.set_text("")
            stream_link = sourceSelect.ctx.source_select(uncached, sources, item_information)
        else:
            stream_link = helpers.Resolverhelper().resolve_silent_or_visible(
                sources, ii, ctx.pack_select, ctx.overwrite_cache=ctx.overwrite_cache
            )
            if stream_link is None:
                g.close_busy_dialog()
                g.close_all_dialogs()
                g.notification(g.ADDON_NAME, g.get_language_string(30032), time=5000)

        if not stream_link or stream_link == "none":
            raise NoPlayableSourcesException

        from resources.lib.modules import player

        try:
            prism_player = player.PrismPlayer()
            prism_player.play_source(stream_link, item_information, resume_time=resume_time)
        finally:
            if background:
                try:
                    background.close()
                finally:
                    del background
            del prism_player

    except NoPlayableSourcesException:
        try:
            background.close()
            del background
        except (UnboundLocalError, AttributeError):
            pass
        try:
            resolver_window.close()
            del resolver_window
        except (UnboundLocalError, AttributeError):
            pass

        g.cancel_playback()

    finally:
        g.clear_runtime_setting("playback.pipeline_busy")

@register_action("playFromRandomPoint")
def handle_playFromRandomPoint(ctx: DispatchContext) -> None:
    from resources.lib.modules import smartPlay

    smartPlay.SmartPlay(ctx.action_args).play_from_random_point()

@register_action("preScrape")
def handle_preScrape(ctx: DispatchContext) -> None:

    from resources.lib.database.skinManager import SkinManager
    from resources.lib.modules import helpers

    try:
        from resources.lib.common import tools

        item_information = tools.get_item_information(ctx.action_args)

        # Get Sources
        sources_helper = helpers.SourcesHelper()
        sources_result = sources_helper.get_sources(ctx.action_args)
        if sources_result is None:
            return
        uncached, sources_list, ii = sources_result

        # Sort sources
        sources = sources_helper.sort_sources(ii, sources_list)
        if sources is None:
            return

        if item_information["info"]["ctx.mediatype"] == g.MEDIA_EPISODE:
            ctx.source_select_style = "Episodes"
        else:
            ctx.source_select_style = "Movie"
        if g.get_int_setting(f"general.playstyle{ctx.source_select_style}") == 0 and sources:
            from resources.lib.modules import resolver

            helpers.Resolverhelper().resolve_silent_or_visible(sources, ii, ctx.pack_select)
    finally:
        g.set_runtime_setting("tempSilent", False)

    g.log("Pre-scraping completed")

@register_action("runPlayerDialogs")
def handle_runPlayerDialogs(ctx: DispatchContext) -> None:
    from resources.lib.modules.player import PlayerDialogs

    try:
        player_dialogs = PlayerDialogs()
        player_dialogs.display_dialog()
    finally:
        del player_dialogs

@register_action("showSkipSegment")
def handle_showSkipSegment(ctx: DispatchContext) -> None:
    from resources.lib.gui.windows.skip_segment import SkipSegment
    from resources.lib.database.skinManager import SkinManager

    try:
        window = SkipSegment(
            *SkinManager().confirm_skin_path("skip_segment.xml"),
            segment_type=ctx.params.get("segment", "intro"),
            segment_end=ctx.params.get("end", 0),
        )
        window.doModal()
    finally:
        del window

@register_action("shufflePlay")
def handle_shufflePlay(ctx: DispatchContext) -> None:
    from resources.lib.modules import smartPlay

    smartPlay.SmartPlay(ctx.action_args).shuffle_play()

import contextlib
import time
import traceback
from functools import cached_property
from functools import wraps
from urllib import parse

import xbmcgui

from . import valid_id_or_none
from resources.lib.common import tools
from resources.lib.common.thread_pool import ThreadPool
from resources.lib.database.cache import use_cache
from resources.lib.indexers.apibase import ApiBase
from resources.lib.modules.exceptions import RanOnceAlready
from resources.lib.modules.global_lock import GlobalLock
from resources.lib.modules.globals import g


def _tvdb_error_message(response):
    """Resolve a log-safe message from a non-success TVDB v4 HTTP response."""
    message = TVDBAPI.http_codes.get(response.status_code, f"HTTP {response.status_code}")
    try:
        body = response.json()
    except (ValueError, AttributeError, TypeError):
        return message
    if not isinstance(body, dict):
        return message
    if body.get("message"):
        return body["message"]
    if body.get("status") == "error":
        return body.get("message") or message
    # TVDB v3 legacy envelope (defensive).
    if body.get("Error"):
        return body["Error"]
    return message


def tvdb_guard_response(func):
    @wraps(func)
    def wrapper(*args, **kwarg):
        method_class = args[0]
        import requests

        try:
            response = func(*args, **kwarg)
            if response.status_code in [200, 201]:
                return response

            if response.status_code == 401:
                with contextlib.suppress(RanOnceAlready):
                    with GlobalLock("tvdb.oauth", run_once=True, check_sum=method_class.jwToken) as lock:
                        method_class.init_v4_token()
                if method_class.jwToken is not None:
                    return func(*args, **kwarg)

            error_message = _tvdb_error_message(response)

            g.log(
                f"TVDB returned a {response.status_code} ({error_message}): while requesting {response.url}",
                "warning" if response.status_code != 404 else "debug",
            )

            return None
        except requests.exceptions.ConnectionError:
            return None
        except Exception:
            xbmcgui.Dialog().notification(g.ADDON_NAME, g.get_language_string(30024).format("TVDB"))
            if g.get_runtime_setting("run.mode") == "test":
                raise
            else:
                g.log_stacktrace()
            return None

    return wrapper


def wrap_tvdb_object(func):
    @wraps(func)
    def wrapper(*args, **kwarg):
        return {"tvdb_object": tvdb_art_sorter(func(*args, **kwarg))}

    return wrapper


def tvdb_art_sorter(item):
    if not item or not item.get("art"):
        return item

    for art_type in ["banner", "poster", "fanart"]:
        if art_type not in item.get("art"):
            continue

        item["art"][art_type] = sorted(
            item["art"][art_type], key=lambda k: (k["language"], k["rating"], k["url"]), reverse=True
        )
    return item


class TVDBAPI(ApiBase):
    v4BaseUrl = "https://api4.thetvdb.com/v4/"
    v4ArtworkUrlPrefix = "https://artworks.thetvdb.com"
    movie_artwork_types = {
        14: "poster",
        15: "fanart",
        16: "banner",
        24: "clearart",
        25: "clearlogo",
    }
    series_artwork_types = {
        1: "banner",
        2: "poster",
        3: "fanart",
        22: "clearart",
        23: "clearlogo",
    }
    season_artwork_types = {
        6: "banner",
        7: "poster",
    }
    normalization = [
        ("imdbId", ("imdbnumber", "imdb_id"), lambda i: valid_id_or_none(i)),
        ("id", "tvdb_id", None),
        (
            None,
            "rating.tvdb",
            (
                ("siteRating", "siteRatingCount"),
                lambda r, c: {"rating": tools.safe_round(r, 2), "votes": c},
            ),
        ),
        ("firstAired", ("premiered", "aired"), lambda t: g.validate_date(t)),
        ("overview", ("plot", "plotoutline"), None),
        ("mediatype", "mediatype", None),
    ]

    v4_show_normalization = tools.extend_array(
        [
            ("name", ("title", "tvshowtitle"), None),
            (
                "year",
                "year",
                lambda y: str(y)[:4] if y is not None else None,
            ),
            ("status", "status", lambda s: s.get("name") if isinstance(s, dict) else s),
            (
                "averageRuntime",
                "runtime",
                lambda d: int(d) * 60 if d is not None and str(d).isdigit() else None,
            ),
            (("originalNetwork", "name"), "studio", None),
            (
                "genres",
                "genre",
                lambda genres: sorted(
                    {genre.get("name") for genre in genres if isinstance(genre, dict) and genre.get("name")}
                ),
            ),
            (
                "contentRatings",
                "mpaa",
                lambda ratings: next(
                    (
                        rating.get("name")
                        for rating in ratings
                        if isinstance(rating, dict) and (rating.get("country") or "").lower() in ("usa", "us")
                    ),
                    ratings[0].get("name") if ratings and isinstance(ratings[0], dict) else None,
                ),
            ),
            ("originalLanguage", "language", None),
            (
                "aliases",
                "aliases",
                lambda aliases: [alias.get("name") for alias in aliases if isinstance(alias, dict) and alias.get("name")],
            ),
            (
                "originalCountry",
                "country_origin",
                lambda t: t.upper() if t is not None else None,
            ),
            (
                None,
                "rating.tvdb",
                (("score",), lambda s: {"rating": tools.safe_round(s, 2), "votes": 0}),
            ),
            (
                "remoteIds",
                "imdb_id",
                lambda ids: next(
                    (
                        valid_id_or_none(remote.get("id"))
                        for remote in ids
                        if isinstance(remote, dict) and (remote.get("sourceName") or "").upper() == "IMDB"
                    ),
                    None,
                ),
            ),
        ],
        normalization,
    )

    v4_episode_normalization = tools.extend_array(
        [
            ("name", "title", None),
            ("seriesId", "tvdb_show_id", None),
            ("number", ("episode", "sortepisode"), None),
            ("seasonNumber", ("season", "sortseason"), None),
            ("overview", "plot", None),
            (
                "runtime",
                "runtime",
                lambda d: int(d) * 60 if d is not None and str(d).isdigit() else None,
            ),
            (
                "contentRatings",
                "mpaa",
                lambda ratings: next(
                    (
                        rating.get("name")
                        for rating in ratings
                        if isinstance(rating, dict) and (rating.get("country") or "").lower() in ("usa", "us")
                    ),
                    ratings[0].get("name") if ratings and isinstance(ratings[0], dict) else None,
                ),
            ),
        ],
        normalization,
    )

    movie_normalization = tools.extend_array(
        [
            ("name", ("title", "sorttitle"), None),
            ("overview", ("plot", "plotoutline"), None),
            ("year", "year", lambda y: int(y) if y and str(y).isdigit() else None),
            ("runtime", "runtime", lambda d: int(d) * 60 if d is not None and str(d).isdigit() else None),
            ("id", "tvdb_id", None),
        ],
        normalization,
    )

    meta_objects = {
        "movie": movie_normalization,
        "tvshow": v4_show_normalization,
        "episode": v4_episode_normalization,
    }

    # These are a duplicate of the TMDB codes, should be used as a very rough reference
    # Until we can find a good source from TVDB about their codes we can use this in place
    http_codes = {
        200: "Success",
        201: "Success - new resource created (POST)",
        401: "Returned if your JWT token is missing or expired",
        404: "Returned if the given ID does not exist.",
        409: "Returned if requested record could not be updated/deleted",
        405: "Missing query params are given",
        422: "Invalid query params provided",
        500: "Internal Server Error",
        501: "Not Implemented",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }

    supported_languages = [
        {"id": 101, "abbreviation": "aa", "name": "Afaraf", "englishName": "Afar"},
        {
            "id": 102,
            "abbreviation": "ab",
            "name": "аҧсуа бызшәа",
            "englishName": "Abkhaz",
        },
        {
            "id": 103,
            "abbreviation": "af",
            "name": "Afrikaans",
            "englishName": "Afrikaans",
        },
        {"id": 104, "abbreviation": "ak", "name": "Akan", "englishName": "Akan"},
        {"id": 105, "abbreviation": "am", "name": "አማርኛ", "englishName": "Amharic"},
        {
            "id": 106,
            "abbreviation": "ar",
            "name": "العربية",
            "englishName": "Arabic",
        },
        {
            "id": 107,
            "abbreviation": "an",
            "name": "aragonés",
            "englishName": "Aragonese",
        },
        {
            "id": 108,
            "abbreviation": "as",
            "name": "অসমীয়া",
            "englishName": "Assamese",
        },
        {
            "id": 109,
            "abbreviation": "av",
            "name": "авар мацӀ",
            "englishName": "Avaric",
        },
        {
            "id": 110,
            "abbreviation": "ae",
            "name": "avesta",
            "englishName": "Avestan",
        },
        {
            "id": 111,
            "abbreviation": "ay",
            "name": "aymar aru",
            "englishName": "Aymara",
        },
        {
            "id": 112,
            "abbreviation": "az",
            "name": "azərbaycan dili",
            "englishName": "Azerbaijani",
        },
        {
            "id": 113,
            "abbreviation": "ba",
            "name": "башҡорт теле",
            "englishName": "Bashkir",
        },
        {
            "id": 114,
            "abbreviation": "bm",
            "name": "bamanankan",
            "englishName": "Bambara",
        },
        {
            "id": 115,
            "abbreviation": "be",
            "name": "беларуская мова",
            "englishName": "Belarusian",
        },
        {
            "id": 116,
            "abbreviation": "bn",
            "name": "বাংলা",
            "englishName": "Bengali",
        },
        {
            "id": 117,
            "abbreviation": "bh",
            "name": "भोजपुरी",
            "englishName": "Bihari",
        },
        {
            "id": 118,
            "abbreviation": "bi",
            "name": "Bislama",
            "englishName": "Bislama",
        },
        {
            "id": 119,
            "abbreviation": "bo",
            "name": "བོད་ཡིག",
            "englishName": "Tibetan Standard",
        },
        {
            "id": 120,
            "abbreviation": "bs",
            "name": "bosanski jezik",
            "englishName": "Bosnian",
        },
        {
            "id": 121,
            "abbreviation": "br",
            "name": "brezhoneg",
            "englishName": "Breton",
        },
        {
            "id": 122,
            "abbreviation": "bg",
            "name": "български език",
            "englishName": "Bulgarian",
        },
        {
            "id": 123,
            "abbreviation": "ca",
            "name": "català",
            "englishName": "Catalan",
        },
        {"id": 28, "abbreviation": "cs", "name": "čeština", "englishName": "Czech"},
        {
            "id": 124,
            "abbreviation": "ch",
            "name": "Chamoru",
            "englishName": "Chamorro",
        },
        {
            "id": 125,
            "abbreviation": "ce",
            "name": "нохчийн мотт",
            "englishName": "Chechen",
        },
        {
            "id": 126,
            "abbreviation": "cu",
            "name": "ѩзыкъ словѣньскъ",
            "englishName": "Old Church Slavonic",
        },
        {
            "id": 127,
            "abbreviation": "cv",
            "name": "чӑваш чӗлхи",
            "englishName": "Chuvash",
        },
        {
            "id": 128,
            "abbreviation": "kw",
            "name": "Kernewek",
            "englishName": "Cornish",
        },
        {
            "id": 129,
            "abbreviation": "co",
            "name": "corsu",
            "englishName": "Corsican",
        },
        {"id": 130, "abbreviation": "cr", "name": "ᓀᐦᐃᔭᐍᐏᐣ", "englishName": "Cree"},
        {
            "id": 131,
            "abbreviation": "cy",
            "name": "Cymraeg",
            "englishName": "Welsh",
        },
        {"id": 10, "abbreviation": "da", "name": "dansk", "englishName": "Danish"},
        {
            "id": 14,
            "abbreviation": "de",
            "name": "Deutsch",
            "englishName": "German",
        },
        {
            "id": 132,
            "abbreviation": "dv",
            "name": "ދިވެހި",
            "englishName": "Divehi",
        },
        {
            "id": 133,
            "abbreviation": "dz",
            "name": "རྫོང་ཁ",
            "englishName": "Dzongkha",
        },
        {
            "id": 20,
            "abbreviation": "el",
            "name": "ελληνική γλώσσα",
            "englishName": "Greek",
        },
        {
            "id": 7,
            "abbreviation": "en",
            "name": "English",
            "englishName": "English",
        },
        {
            "id": 134,
            "abbreviation": "eo",
            "name": "Esperanto",
            "englishName": "Esperanto",
        },
        {
            "id": 135,
            "abbreviation": "et",
            "name": "eesti",
            "englishName": "Estonian",
        },
        {
            "id": 136,
            "abbreviation": "eu",
            "name": "euskara",
            "englishName": "Basque",
        },
        {"id": 137, "abbreviation": "ee", "name": "Eʋegbe", "englishName": "Ewe"},
        {
            "id": 138,
            "abbreviation": "fo",
            "name": "føroyskt",
            "englishName": "Faroese",
        },
        {
            "id": 139,
            "abbreviation": "fa",
            "name": "فارسی",
            "englishName": "Persian",
        },
        {
            "id": 140,
            "abbreviation": "fj",
            "name": "vosa Vakaviti",
            "englishName": "Fijian",
        },
        {"id": 11, "abbreviation": "fi", "name": "suomi", "englishName": "Finnish"},
        {
            "id": 17,
            "abbreviation": "fr",
            "name": "français",
            "englishName": "French",
        },
        {
            "id": 141,
            "abbreviation": "fy",
            "name": "Frysk",
            "englishName": "Western Frisian",
        },
        {
            "id": 142,
            "abbreviation": "ff",
            "name": "Fulfulde",
            "englishName": "Fula",
        },
        {
            "id": 143,
            "abbreviation": "gd",
            "name": "Gàidhlig",
            "englishName": "Scottish Gaelic",
        },
        {
            "id": 144,
            "abbreviation": "ga",
            "name": "Gaeilge",
            "englishName": "Irish",
        },
        {
            "id": 145,
            "abbreviation": "gl",
            "name": "galego",
            "englishName": "Galician",
        },
        {"id": 146, "abbreviation": "gv", "name": "Gaelg", "englishName": "Manx"},
        {
            "id": 147,
            "abbreviation": "gn",
            "name": "Avañe'ẽ",
            "englishName": "Guaraní",
        },
        {
            "id": 148,
            "abbreviation": "gu",
            "name": "ગુજરાતી",
            "englishName": "Gujarati",
        },
        {
            "id": 149,
            "abbreviation": "ht",
            "name": "Kreyòl ayisyen",
            "englishName": "Haitian",
        },
        {"id": 150, "abbreviation": "ha", "name": "هَوُسَ", "englishName": "Hausa"},
        {"id": 24, "abbreviation": "he", "name": "עברית", "englishName": "Hebrew"},
        {
            "id": 151,
            "abbreviation": "hz",
            "name": "Otjiherero",
            "englishName": "Herero",
        },
        {"id": 152, "abbreviation": "hi", "name": "हिन्दी", "englishName": "Hindi"},
        {
            "id": 153,
            "abbreviation": "ho",
            "name": "Hiri Motu",
            "englishName": "Hiri Motu",
        },
        {
            "id": 31,
            "abbreviation": "hr",
            "name": "hrvatski jezik",
            "englishName": "Croatian",
        },
        {
            "id": 19,
            "abbreviation": "hu",
            "name": "Magyar",
            "englishName": "Hungarian",
        },
        {
            "id": 154,
            "abbreviation": "hy",
            "name": "Հայերեն",
            "englishName": "Armenian",
        },
        {
            "id": 155,
            "abbreviation": "ig",
            "name": "Asụsụ Igbo",
            "englishName": "Igbo",
        },
        {"id": 156, "abbreviation": "io", "name": "Ido", "englishName": "Ido"},
        {
            "id": 157,
            "abbreviation": "ii",
            "name": "Nuosuhxop",
            "englishName": "Nuosu",
        },
        {
            "id": 158,
            "abbreviation": "iu",
            "name": "ᐃᓄᒃᑎᑐᑦ",
            "englishName": "Inuktitut",
        },
        {
            "id": 159,
            "abbreviation": "ie",
            "name": "Interlingue",
            "englishName": "Interlingue",
        },
        {
            "id": 160,
            "abbreviation": "ia",
            "name": "Interlingua",
            "englishName": "Interlingua",
        },
        {
            "id": 161,
            "abbreviation": "id",
            "name": "Bahasa Indonesia",
            "englishName": "Indonesian",
        },
        {
            "id": 162,
            "abbreviation": "ik",
            "name": "Iñupiaq",
            "englishName": "Inupiaq",
        },
        {
            "id": 163,
            "abbreviation": "is",
            "name": "Íslenska",
            "englishName": "Icelandic",
        },
        {
            "id": 15,
            "abbreviation": "it",
            "name": "italiano",
            "englishName": "Italian",
        },
        {
            "id": 164,
            "abbreviation": "jv",
            "name": "basa Jawa",
            "englishName": "Javanese",
        },
        {"id": 25, "abbreviation": "ja", "name": "日本語", "englishName": "Japanese"},
        {
            "id": 165,
            "abbreviation": "kl",
            "name": "kalaallisut",
            "englishName": "Kalaallisut",
        },
        {
            "id": 166,
            "abbreviation": "kn",
            "name": "ಕನ್ನಡ",
            "englishName": "Kannada",
        },
        {
            "id": 167,
            "abbreviation": "ks",
            "name": "कश्मीरी",
            "englishName": "Kashmiri",
        },
        {
            "id": 168,
            "abbreviation": "ka",
            "name": "ქართული",
            "englishName": "Georgian",
        },
        {
            "id": 169,
            "abbreviation": "kr",
            "name": "Kanuri",
            "englishName": "Kanuri",
        },
        {
            "id": 170,
            "abbreviation": "kk",
            "name": "қазақ тілі",
            "englishName": "Kazakh",
        },
        {"id": 171, "abbreviation": "km", "name": "ខ្មែរ", "englishName": "Khmer"},
        {
            "id": 172,
            "abbreviation": "ki",
            "name": "Gĩkũyũ",
            "englishName": "Kikuyu",
        },
        {
            "id": 173,
            "abbreviation": "rw",
            "name": "Ikinyarwanda",
            "englishName": "Kinyarwanda",
        },
        {
            "id": 174,
            "abbreviation": "ky",
            "name": "кыргыз тили",
            "englishName": "Kirghiz",
        },
        {
            "id": 175,
            "abbreviation": "kv",
            "name": "коми кыв",
            "englishName": "Komi",
        },
        {
            "id": 176,
            "abbreviation": "kg",
            "name": "KiKongo",
            "englishName": "Kongo",
        },
        {"id": 32, "abbreviation": "ko", "name": "한국어", "englishName": "Korean"},
        {
            "id": 177,
            "abbreviation": "kj",
            "name": "Kuanyama",
            "englishName": "Kwanyama",
        },
        {
            "id": 178,
            "abbreviation": "ku",
            "name": "Kurdî",
            "englishName": "Kurdish",
        },
        {"id": 179, "abbreviation": "lo", "name": "ພາສາລາວ", "englishName": "Lao"},
        {"id": 180, "abbreviation": "la", "name": "latine", "englishName": "Latin"},
        {
            "id": 181,
            "abbreviation": "lv",
            "name": "latviešu valoda",
            "englishName": "Latvian",
        },
        {
            "id": 182,
            "abbreviation": "li",
            "name": "Limburgs",
            "englishName": "Limburgish",
        },
        {
            "id": 183,
            "abbreviation": "ln",
            "name": "Lingála",
            "englishName": "Lingala",
        },
        {
            "id": 184,
            "abbreviation": "lt",
            "name": "lietuvių kalba",
            "englishName": "Lithuanian",
        },
        {
            "id": 185,
            "abbreviation": "lb",
            "name": "Lëtzebuergesch",
            "englishName": "Luxembourgish",
        },
        {
            "id": 186,
            "abbreviation": "lu",
            "name": "Luba-Katanga",
            "englishName": "Luba-Katanga",
        },
        {
            "id": 187,
            "abbreviation": "lg",
            "name": "Luganda",
            "englishName": "Luganda",
        },
        {
            "id": 188,
            "abbreviation": "mh",
            "name": "Kajin M̧ajeļ",
            "englishName": "Marshallese",
        },
        {
            "id": 189,
            "abbreviation": "ml",
            "name": "മലയാളം",
            "englishName": "Malayalam",
        },
        {
            "id": 190,
            "abbreviation": "mr",
            "name": "मराठी",
            "englishName": "Marathi",
        },
        {
            "id": 191,
            "abbreviation": "mk",
            "name": "македонски јазик",
            "englishName": "Macedonian",
        },
        {
            "id": 192,
            "abbreviation": "mg",
            "name": "Malagasy fiteny",
            "englishName": "Malagasy",
        },
        {
            "id": 193,
            "abbreviation": "mt",
            "name": "Malti",
            "englishName": "Maltese",
        },
        {
            "id": 194,
            "abbreviation": "mn",
            "name": "монгол",
            "englishName": "Mongolian",
        },
        {
            "id": 195,
            "abbreviation": "mi",
            "name": "te reo Māori",
            "englishName": "Māori",
        },
        {
            "id": 196,
            "abbreviation": "ms",
            "name": "bahasa Melayu",
            "englishName": "Malay",
        },
        {
            "id": 197,
            "abbreviation": "my",
            "name": "Burmese",
            "englishName": "Burmese",
        },
        {
            "id": 198,
            "abbreviation": "na",
            "name": "Ekakairũ Naoero",
            "englishName": "Nauru",
        },
        {
            "id": 199,
            "abbreviation": "nv",
            "name": "Diné bizaad",
            "englishName": "Navajo",
        },
        {
            "id": 200,
            "abbreviation": "nr",
            "name": "isiNdebele",
            "englishName": "South Ndebele",
        },
        {
            "id": 201,
            "abbreviation": "nd",
            "name": "isiNdebele",
            "englishName": "North Ndebele",
        },
        {
            "id": 202,
            "abbreviation": "ng",
            "name": "Owambo",
            "englishName": "Ndonga",
        },
        {
            "id": 203,
            "abbreviation": "ne",
            "name": "नेपाली",
            "englishName": "Nepali",
        },
        {
            "id": 13,
            "abbreviation": "nl",
            "name": "Nederlands",
            "englishName": "Dutch",
        },
        {
            "id": 9,
            "abbreviation": "no",
            "name": "Norsk bokmål",
            "englishName": "Norwegian",
        },
        {
            "id": 206,
            "abbreviation": "ny",
            "name": "chiCheŵa",
            "englishName": "Chichewa",
        },
        {
            "id": 207,
            "abbreviation": "oc",
            "name": "occitan",
            "englishName": "Occitan",
        },
        {
            "id": 208,
            "abbreviation": "oj",
            "name": "ᐊᓂᔑᓈᐯᒧᐎᓐ",
            "englishName": "Ojibwe",
        },
        {"id": 209, "abbreviation": "or", "name": "ଓଡ଼ିଆ", "englishName": "Oriya"},
        {
            "id": 210,
            "abbreviation": "om",
            "name": "Afaan Oromoo",
            "englishName": "Oromo",
        },
        {
            "id": 211,
            "abbreviation": "os",
            "name": "ирон æвзаг",
            "englishName": "Ossetian",
        },
        {
            "id": 212,
            "abbreviation": "pa",
            "name": "ਪੰਜਾਬੀ",
            "englishName": "Panjabi",
        },
        {"id": 213, "abbreviation": "pi", "name": "पाऴि", "englishName": "Pāli"},
        {
            "id": 18,
            "abbreviation": "pl",
            "name": "język polski",
            "englishName": "Polish",
        },
        {
            "id": 214,
            "abbreviation": "pt",
            "name": "Português - Portugal",
            "englishName": "Portuguese - Portugal",
        },
        {
            "id": 26,
            "abbreviation": "pt",
            "name": "Português - Brasil",
            "englishName": "Portuguese - Brazil",
        },
        {"id": 215, "abbreviation": "ps", "name": "پښتو", "englishName": "Pashto"},
        {
            "id": 216,
            "abbreviation": "qu",
            "name": "Runa Simi",
            "englishName": "Quechua",
        },
        {
            "id": 217,
            "abbreviation": "rm",
            "name": "rumantsch grischun",
            "englishName": "Romansh",
        },
        {
            "id": 218,
            "abbreviation": "ro",
            "name": "limba română",
            "englishName": "Romanian",
        },
        {
            "id": 219,
            "abbreviation": "rn",
            "name": "Ikirundi",
            "englishName": "Kirundi",
        },
        {
            "id": 22,
            "abbreviation": "ru",
            "name": "русский язык",
            "englishName": "Russian",
        },
        {
            "id": 220,
            "abbreviation": "sg",
            "name": "yângâ tî sängö",
            "englishName": "Sango",
        },
        {
            "id": 221,
            "abbreviation": "sa",
            "name": "संस्कृतम्",
            "englishName": "Sanskrit",
        },
        {
            "id": 222,
            "abbreviation": "si",
            "name": "සිංහල",
            "englishName": "Sinhala",
        },
        {
            "id": 30,
            "abbreviation": "sk",
            "name": "slovenčina",
            "englishName": "Slovak",
        },
        {
            "id": 223,
            "abbreviation": "sl",
            "name": "slovenski jezik",
            "englishName": "Slovene",
        },
        {
            "id": 224,
            "abbreviation": "se",
            "name": "Davvisámegiella",
            "englishName": "Northern Sami",
        },
        {
            "id": 225,
            "abbreviation": "sm",
            "name": "gagana fa'a Samoa",
            "englishName": "Samoan",
        },
        {
            "id": 226,
            "abbreviation": "sn",
            "name": "chiShona",
            "englishName": "Shona",
        },
        {
            "id": 227,
            "abbreviation": "sd",
            "name": "सिन्धी",
            "englishName": "Sindhi",
        },
        {
            "id": 228,
            "abbreviation": "so",
            "name": "Soomaaliga",
            "englishName": "Somali",
        },
        {
            "id": 229,
            "abbreviation": "st",
            "name": "Sesotho",
            "englishName": "Southern Sotho",
        },
        {
            "id": 16,
            "abbreviation": "es",
            "name": "español",
            "englishName": "Spanish",
        },
        {
            "id": 230,
            "abbreviation": "sq",
            "name": "gjuha shqipe",
            "englishName": "Albanian",
        },
        {
            "id": 231,
            "abbreviation": "sc",
            "name": "sardu",
            "englishName": "Sardinian",
        },
        {
            "id": 232,
            "abbreviation": "sr",
            "name": "српски језик",
            "englishName": "Serbian",
        },
        {
            "id": 233,
            "abbreviation": "ss",
            "name": "SiSwati",
            "englishName": "Swati",
        },
        {
            "id": 234,
            "abbreviation": "su",
            "name": "Basa Sunda",
            "englishName": "Sundanese",
        },
        {
            "id": 235,
            "abbreviation": "sw",
            "name": "Kiswahili",
            "englishName": "Swahili",
        },
        {
            "id": 8,
            "abbreviation": "sv",
            "name": "svenska",
            "englishName": "Swedish",
        },
        {
            "id": 236,
            "abbreviation": "ty",
            "name": "Reo Tahiti",
            "englishName": "Tahitian",
        },
        {"id": 237, "abbreviation": "ta", "name": "தமிழ்", "englishName": "Tamil"},
        {
            "id": 238,
            "abbreviation": "tt",
            "name": "татар теле",
            "englishName": "Tatar",
        },
        {
            "id": 239,
            "abbreviation": "te",
            "name": "తెలుగు",
            "englishName": "Telugu",
        },
        {"id": 240, "abbreviation": "tg", "name": "тоҷикӣ", "englishName": "Tajik"},
        {
            "id": 241,
            "abbreviation": "tl",
            "name": "Wikang Tagalog",
            "englishName": "Tagalog",
        },
        {"id": 242, "abbreviation": "th", "name": "ไทย", "englishName": "Thai"},
        {
            "id": 243,
            "abbreviation": "ti",
            "name": "ትግርኛ",
            "englishName": "Tigrinya",
        },
        {
            "id": 244,
            "abbreviation": "to",
            "name": "faka Tonga",
            "englishName": "Tonga",
        },
        {
            "id": 245,
            "abbreviation": "tn",
            "name": "Setswana",
            "englishName": "Tswana",
        },
        {
            "id": 246,
            "abbreviation": "ts",
            "name": "Xitsonga",
            "englishName": "Tsonga",
        },
        {
            "id": 247,
            "abbreviation": "tk",
            "name": "Türkmen",
            "englishName": "Turkmen",
        },
        {
            "id": 21,
            "abbreviation": "tr",
            "name": "Türkçe",
            "englishName": "Turkish",
        },
        {"id": 248, "abbreviation": "tw", "name": "Twi", "englishName": "Twi"},
        {
            "id": 249,
            "abbreviation": "ug",
            "name": "Uyƣurqə",
            "englishName": "Uighur",
        },
        {
            "id": 250,
            "abbreviation": "uk",
            "name": "українська мова",
            "englishName": "Ukrainian",
        },
        {"id": 251, "abbreviation": "ur", "name": "اردو", "englishName": "Urdu"},
        {"id": 252, "abbreviation": "uz", "name": "Ozbek", "englishName": "Uzbek"},
        {
            "id": 253,
            "abbreviation": "ve",
            "name": "Tshivenḓa",
            "englishName": "Venda",
        },
        {
            "id": 254,
            "abbreviation": "vi",
            "name": "Tiếng Việt",
            "englishName": "Vietnamese",
        },
        {
            "id": 255,
            "abbreviation": "vo",
            "name": "Volapük",
            "englishName": "Volapük",
        },
        {
            "id": 256,
            "abbreviation": "wa",
            "name": "walon",
            "englishName": "Walloon",
        },
        {"id": 257, "abbreviation": "wo", "name": "Wollof", "englishName": "Wolof"},
        {
            "id": 258,
            "abbreviation": "xh",
            "name": "isiXhosa",
            "englishName": "Xhosa",
        },
        {
            "id": 259,
            "abbreviation": "yi",
            "name": "ייִדיש",
            "englishName": "Yiddish",
        },
        {
            "id": 260,
            "abbreviation": "yo",
            "name": "Yorùbá",
            "englishName": "Yoruba",
        },
        {
            "id": 261,
            "abbreviation": "za",
            "name": "Saɯ cueŋƅ",
            "englishName": "Zhuang",
        },
        {
            "id": 27,
            "abbreviation": "zh",
            "name": "大陆简体",
            "englishName": "Chinese - China",
        },
        {"id": 262, "abbreviation": "zu", "name": "isiZulu", "englishName": "Zulu"},
    ]

    def __init__(self):

        self._load_settings()

        self.lang_code = g.get_language_code(False)

        self.languages = (
            [None, self.lang_code]
            if self.lang_code != "en" and any(self.lang_code == i["abbreviation"] for i in self.supported_languages)
            else [None]
        )

        if not self.jwToken:
            self.init_v4_token()
        else:
            self.try_refresh_token()

        self.preferred_artwork_size = g.get_int_setting("artwork.preferredsize")

    @cached_property
    def meta_hash(self):
        return tools.md5_hash(
            (
                self.lang_code,
                self.v4BaseUrl,
                self.v4ArtworkUrlPrefix,
                self.movie_artwork_types,
                self.series_artwork_types,
                self.season_artwork_types,
                self.preferred_artwork_size,
            )
        )

    @cached_property
    def session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3 import Retry

        session = requests.Session()
        retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries, pool_maxsize=100))
        return session

    @cached_property
    def threadpool(self):
        return ThreadPool()

    def try_refresh_token(self, force=False):
        if not force and self.tokenExpires > float(time.time()):
            return
        try:
            with GlobalLock(self.__class__.__name__, True, self.jwToken) as lock:
                g.log("TVDB token requires refreshing...")
                self.init_v4_token()
                g.log("Refreshed TVDB token")
        except RanOnceAlready:
            return

    def _load_settings(self):
        from resources.lib.database.keys import get_api_key

        self.apiKey = get_api_key("TVDB") or ""
        self.jwToken = g.get_setting("tvdb.jw")
        self.tokenExpires = g.get_float_setting("tvdb.expiry")

    def _save_settings(self, response):
        if "token" in response:
            g.set_setting("tvdb.jw", response["token"])
            self.jwToken = response["token"]
            self.tokenExpires = time.time() + (24 * (60 * 60))
            g.set_setting("tvdb.expiry", str(self.tokenExpires))

    def init_token(self):
        self.init_v4_token()

    def init_v4_token(self):
        try:
            with GlobalLock(f"{self.__class__.__name__}.v4", True):
                response = self.session.post(
                    parse.urljoin(self.v4BaseUrl, "login"),
                    json={"apikey": self.apiKey},
                    headers={"Content-Type": "application/json"},
                ).json()
                token = tools.safe_dict_get(response, "data", "token") or response.get("token")
                if token:
                    self._save_settings({"token": token})
        except RanOnceAlready:
            return

    def _v4_language_code(self, language=None):
        lang = language or self.lang_code or "en"
        if lang == "en":
            return "eng"
        if len(lang) == 2:
            return lang
        return lang[:3]

    def _v4_art_language(self, language):
        if not language:
            return "en"
        if language == "eng":
            return "en"
        return language[:2]

    def _absolute_v4_image_path(self, path, thumbnail=None):
        if not path:
            return None
        if path.startswith("http"):
            return path
        if self.preferred_artwork_size == 2 and thumbnail:
            path = thumbnail
        return "/".join([self.v4ArtworkUrlPrefix.strip("/"), path.strip("/")])

    @tvdb_guard_response
    def get_v4(self, url, **params):
        language = params.pop("language", params.pop("lang", None))
        timeout = params.pop("timeout", 10)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.jwToken:
            headers["Authorization"] = f"Bearer {self.jwToken}"
        if language is not None:
            headers["Accept-Language"] = self._v4_language_code(language)
        return self.session.get(
            parse.urljoin(self.v4BaseUrl, url),
            params=params,
            headers=headers,
            timeout=timeout,
        )

    def get_v4_json(self, url, **params):
        if not self.jwToken:
            self.init_v4_token()
        response = self.get_v4(url, **params)
        if response is None:
            self.init_v4_token()
            response = self.get_v4(url, **params)
        if response is None:
            return None
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("status") == "error":
                return None
            if isinstance(body, dict) and "data" in body:
                return body["data"]
            return body
        except (ValueError, AttributeError):
            traceback.print_exc()
            g.log(f"Failed to receive JSON from TVDB v4 response - response: {response}", "error")
            return None

    @use_cache()
    def get_v4_json_cached(self, url, **params):
        return self.get_v4_json(url, **params)

    def _sort_v4_art_entries(self, art):
        preferred_lang = self.lang_code or "en"
        for art_type, items in art.items():
            if not isinstance(items, list):
                continue
            items.sort(
                key=lambda i: (
                    i.get("language") == preferred_lang,
                    i.get("rating", 0),
                    i.get("size", 0),
                ),
                reverse=True,
            )
        return art

    def _append_v4_artwork(self, result, artwork, art_type):
        url = self._absolute_v4_image_path(artwork.get("image"), artwork.get("thumbnail"))
        if not url:
            return
        result.setdefault(art_type, []).append(
            {
                "url": url,
                "language": self._v4_art_language(artwork.get("language")),
                "rating": artwork.get("score") or 5,
                "size": artwork.get("width") or 0,
            }
        )

    def _extract_v4_artworks(self, artworks, type_map, *, season_id=None):
        result = {}
        for artwork in artworks or []:
            art_type = type_map.get(artwork.get("type"))
            if not art_type:
                continue
            artwork_season_id = artwork.get("seasonId")
            if season_id is None:
                if artwork_season_id:
                    continue
            elif artwork_season_id != season_id:
                continue
            self._append_v4_artwork(result, artwork, art_type)
        return self._sort_v4_art_entries(result)

    def _extract_v4_movie_art(self, movie):
        return self._extract_v4_artworks(movie.get("artworks"), self.movie_artwork_types)

    def _season_id_for_number(self, series, season_number):
        for season in series.get("seasons") or []:
            if season.get("number") == int(season_number):
                return season.get("id")
        return None

    def _resolve_series_artworks(self, tvdb_id, language, series):
        artworks = (series or {}).get("artworks") or []
        if artworks:
            return artworks
        fallback = self.get_v4_json_cached(f"series/{int(tvdb_id)}/artworks", language=language)
        if isinstance(fallback, list):
            return fallback
        if isinstance(fallback, dict):
            return fallback.get("artworks") or []
        return []

    @use_cache()
    def _get_series_extended(self, tvdb_id, language=None):
        return self.get_v4_json(f"series/{int(tvdb_id)}/extended", language=language)

    def _adapt_v4_series(self, series):
        adapted = dict(series)
        adapted["mediatype"] = "tvshow"
        if isinstance(adapted.get("status"), dict):
            adapted["status"] = adapted["status"].get("name")
        return adapted

    def _handle_v4_cast(self, characters):
        cast = []
        for character in characters or []:
            if not isinstance(character, dict) or not character.get("personName"):
                continue
            cast.append(
                {
                    "name": character.get("personName"),
                    "role": character.get("name") or character.get("peopleType") or "",
                    "thumbnail": self._absolute_v4_image_path(character.get("personImgURL") or character.get("image")),
                    "order": character.get("sort") or 0,
                }
            )
        return sorted(cast, key=lambda member: member.get("order", 0))

    def _build_v4_series_art(self, series, artworks, *, season_number=None):
        art = {}
        if season_number is None:
            art.update(self._extract_v4_artworks(artworks, self.series_artwork_types))
        else:
            season_id = self._season_id_for_number(series, season_number)
            if season_id is not None:
                art.update(self._extract_v4_artworks(artworks, self.season_artwork_types, season_id=season_id))
        if season_number is None and series.get("image") and not art.get("thumb"):
            thumb_url = self._absolute_v4_image_path(series.get("image"))
            if thumb_url:
                art["thumb"] = thumb_url
        return art

    def _get_v4_show_record(self, tvdb_id, language=None, *, include_cast_art=True):
        series = self._get_series_extended(tvdb_id, language)
        if not series:
            return None
        adapted = self._adapt_v4_series(series)
        result = {"info": self._normalize_info(self.v4_show_normalization, adapted)}
        if not include_cast_art:
            return result
        artworks = self._resolve_series_artworks(tvdb_id, language, series)
        result["cast"] = self._handle_v4_cast(series.get("characters"))
        result["art"] = self._build_v4_series_art(series, artworks)
        return result

    def _handle_v4_episode(self, episode, tvdb_show_id):
        if not episode:
            return None
        adapted = dict(episode)
        adapted.update({"mediatype": "episode", "seriesId": int(tvdb_show_id)})
        art = {}
        if adapted.get("image"):
            thumb_url = self._absolute_v4_image_path(adapted.get("image"))
            if thumb_url:
                art["thumb"] = thumb_url
        return {
            "info": self._normalize_info(self.v4_episode_normalization, adapted),
            "art": art,
        }

    def _get_v4_episode_record(self, tvdb_id, season, episode, language=None):
        data = self.get_v4_json_cached(
            f"series/{int(tvdb_id)}/episodes/default",
            language=language,
            page=0,
            season=int(season),
            episodeNumber=int(episode),
        )
        if not data:
            return None
        episodes = data.get("episodes") if isinstance(data, dict) else data
        if not episodes:
            return None
        episode = episodes[0] if isinstance(episodes, list) else episodes
        return self._handle_v4_episode(episode, tvdb_id)

    @wrap_tvdb_object
    def get_show(self, tvdb_id):
        base = self._get_v4_show_record(tvdb_id)
        if not base:
            return None
        for language in self.languages:
            if not language:
                continue
            translated = self._get_v4_show_record(tvdb_id, language, include_cast_art=False)
            if translated:
                base = tools.smart_merge_dictionary(base, translated)
        return base

    @wrap_tvdb_object
    def get_show_art(self, tvdb_id):
        series = self._get_series_extended(tvdb_id)
        if not series:
            return None
        artworks = self._resolve_series_artworks(tvdb_id, None, series)
        art = self._build_v4_series_art(series, artworks)
        return {"art": art} if art else None

    @wrap_tvdb_object
    def get_show_info(self, tvdb_id):
        threadpool = ThreadPool()
        threadpool.put(self._get_v4_show_record, tvdb_id, None, include_cast_art=False)
        for language in self.languages:
            if language:
                threadpool.put(self._get_v4_show_record, tvdb_id, language, include_cast_art=False)
        item = threadpool.wait_completion()
        if not item:
            return None
        series = self._get_series_extended(tvdb_id)
        if series:
            cast = self._handle_v4_cast(series.get("characters"))
            if cast:
                item["cast"] = cast
        return item

    @wrap_tvdb_object
    def get_show_rating(self, tvdb_id):
        series = self._get_series_extended(tvdb_id)
        if not series or series.get("score") is None:
            return None
        return {
            "info": {
                "rating.tvdb": {
                    "rating": tools.safe_round(series["score"], 2),
                    "votes": 0,
                }
            }
        }

    @wrap_tvdb_object
    def get_show_cast(self, tvdb_id):
        series = self._get_series_extended(tvdb_id)
        if not series:
            return None
        cast = self._handle_v4_cast(series.get("characters"))
        return {"cast": cast} if cast else None

    @wrap_tvdb_object
    def get_season_art(self, tvdb_id, season):
        series = self._get_series_extended(tvdb_id)
        if not series:
            return None
        artworks = self._resolve_series_artworks(tvdb_id, None, series)
        art = self._build_v4_series_art(series, artworks, season_number=season)
        return {"art": art} if art else None

    @wrap_tvdb_object
    def get_episode(self, tvdb_id, season, episode):
        item = self.threadpool.map_results(
            self._get_v4_episode_record,
            ((tvdb_id, season, episode, language) for language in self.languages),
        )
        return item or None

    @wrap_tvdb_object
    def get_episode_rating(self, tvdb_id, season, episode):
        item = self._get_v4_episode_record(tvdb_id, season, episode, None)
        if not item or not tools.safe_dict_get(item, "info", "rating.tvdb"):
            return None
        return {"info": tools.filter_dictionary(item["info"], "rating.tvdb")}

    def _handle_v4_movie(self, movie):
        if not movie:
            return None
        movie = dict(movie)
        movie.update({"mediatype": "movie"})
        art = self._extract_v4_movie_art(movie)
        if movie.get("image") and not art.get("thumb"):
            thumb_url = self._absolute_v4_image_path(movie.get("image"))
            if thumb_url:
                art["thumb"] = thumb_url
        result = {
            "art": art,
            "info": self._normalize_info(self.movie_normalization, movie),
        }
        cast = self._handle_v4_cast(movie.get("characters"))
        if cast:
            result["cast"] = cast
        return result

    @wrap_tvdb_object
    def get_movie(self, tvdb_id):
        movie = self.get_v4_json_cached(f"movies/{int(tvdb_id)}/extended")
        return self._handle_v4_movie(movie)

    @wrap_tvdb_object
    def get_movie_art(self, tvdb_id):
        movie = self.get_v4_json_cached(f"movies/{int(tvdb_id)}/extended")
        if not movie:
            return None
        art = self._extract_v4_movie_art(movie)
        if movie.get("image") and not art.get("thumb"):
            thumb_url = self._absolute_v4_image_path(movie.get("image"))
            if thumb_url:
                art["thumb"] = thumb_url
        return {"art": art} if art else None

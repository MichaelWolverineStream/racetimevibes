Public API endpoints
Damian Posener edited this page on Nov 13, 2025 · 26 revisions
These are the endpoints that are currently available on racetime.gg. Click on one to jump to a full description of the API.

All races races/data
Category detail <category>/data
Past category races <category>/races/data
Category leaderboards <category>/leaderboards/data
Race detail <category>/<race>/data
Past user races user/<user>/races/data
User search user/search
General information
The following information applies to all public API requests.

Only HTTP GET requests are allowed.
Authorization for these endpoints is not required.
Response format is always JSON (application/json).
All date/time information is given in UTC.
The X-Date-Exact header
As this site is about racing, timing is important to get right. Along with the JSON response, all API endpoints will also have an X-Date-Exact HTTP response header, which will contain the current date/time in standard ISO format, precise to the last millisecond. You can use this to account for differences between the server's clock and your local one, plus latency to the server.

Although datestamps are given with millisecond prevision, given the nature of the web and latency you should not assume the data given is actually accurate to that degree. As a rule, when displaying data times should be rounded to the nearest decisecond, at most.

URL fields
URLs are always relative to the site root, which in the case of the production site is https://racetime.gg. So if you have a field that reads data_url: /ootr/sensible-fox-1234, the full page URI is https://racetime.gg/ootr/sensible-fox-1234.

Some API data may contain absolute URIs, e.g. Twitch channels.

User data
User objects form part of various API responses and typically always follow the same format. Here is an example of a user object you might find:

{
    "id": "fR42gLweew3pQlm4",
    "full_name": "Mario#5527",
    "name": "Mario",
    "discriminator": "5527",
    "url": "/user/fR42gLweew3pQlm4",
    "avatar": "/media/mario.png",
    "pronouns": "he/him",
    "flair": "monitor supporter",
    "twitch_name": "ItsaMeMario",
    "twitch_channel": "https://www.twitch.tv/itsamemario",
    "can_moderate": false
}
Field breakdown
id: A unique identifier for this user. Will remain the same even if the user changes their display name. IDs are always strings.
A typical user ID is a string of 16 alphanumeric characters.
full_name: A unique user display name, including their original display name and their scrim. Note that some users may not have a scrim, in which case full_name will be the same as name.
name: Just the user's display name. If you don't need an unambigiously unique name for the user, you can use this field. Otherwise you should use full_name.
discriminator: The discriminator (or scrim) is used to disambiguate identical display names. It is typically set to a random 4-digit string. Note that the scrim may start with 0 (e.g. "Bowser#0340"), so you should not treat this field as an integer. May be set to null if the user has no discriminator.
url: URL for the user's profile page.
avatar: URL for the user's avatar picture, or null if the user has no avatar.
pronouns: string indicating the user's preferred pronouns, or null if user has not set any pronouns.
flair: The user's current flair, as a set of space-separated strings. This is used to indicate how the user should be styled. It should not be used to determine logical information, e.g. if a user is a moderator.
Note that flairs are context-sensitive. If you're getting category data for example, you'll never see a "monitor" flair since this only applies in the context of race rooms.
twitch_name: The user's connected Twitch account name, capitalised according to how they've set it on Twitch. Will be null if the user has no connected Twitch account.
twitch_channel: The absolute URI for the user's connected Twitch account channel page. Will be null if the user has no connected Twitch account.
can_moderate: A boolean indicating if the user is a moderator. This is context-sensitive, as moderator status varies per category. If there is no category in context, this will always be false (even if user is staff).
Acceptable use
API data is cached to avoid causing strain on the server, and allow relatively frequent polling. For races running in real-time, it's important to be able to get up-to-date information, and so the cache will automatically invalidate whenever a race or entrant is updated.

Note that CloudFlare currently protects racetime.gg, so if you send too many requests you may have to clear a reCAPTCHA challenge from them. IP whitelisting is only available to trusted individuals.

All races
URL: https://racetime.gg/races/data

Returns a list of all open and ongoing races.

Field breakdown
name: The race's unique name, based on the category and a randomly assigned slug.
category: An object giving brief information about the category. Contains:
name: The name of the category, e.g. "Super Mario 64".
short_name: An abbreviated name, e.g. "OoTR".
slug: Unique category slug (part of the URL).
url: URL for the main category page.
data_url: URL for the category data endpoint, which you can use to obtain more detailed category information.
status: An object giving brief information about the race's status. Contains three keys:
value: A machine-parsable status text. Possible values are:
open
invitational
pending
in_progress
finished
cancelled
partitioned (only for ladder 1v1 races)
verbose_value: A user-parsable status text, e.g. "In progress".
help_text: Describes the status, e.g. "Race is in progress".
url: URL for the main race page.
data_url: URL for the race data endpoint, which you can use to obtain more detailed race information.
goal: An object describing the race goal. Contains:
name: A string value indicating the current goal.
custom: A boolean indicating if the goal name was custom, or one of the pre-set category goals.
info: String containing additional information for race entrants, as set by the monitors.
entrants_count: Total number of entrants in this race (including DQ/forfeits).
entrants_count_finished: Total number of entrants that have finished (not counting DQ/forfeits).
entrants_count_inactive: Total number of entrants that have been DQed or forfieted.
opened_at: Date/time when the race was first created (ISO 8601 date).
started_at: Date/time when the race started, or null if it hasn't started yet (ISO 8601 date).
time_limit: The maximum amount of time the race may be in progress for once it starts (ISO 8601 duration).
Category detail
URL: https://racetime.gg/<category>/data

Replace with the category slug, e.g. ootr.

This endpoint includes all the basic information about the category shown on the webpage, except for past races. Current races are given in a summarised format, full race information must be retrieved individually.

Field breakdown
name: The name of the category, e.g. "Super Mario 64".
short_name: An abbreviated name, e.g. "OoTR".
slug: The unique slug that identifies this category.
url: URL for the category's main page.
data_url: URL for the category data endpoint, i.e. this endpoint.
image: URL for the uploaded image to represent this category, or null if no image is set.
info: Category information blurb, as HTML. This is what appears in the sidebar on the main category page.
streaming_required: Boolean indicating if streaming is required in this category. Moderators may override this on a per-race basis.
owners: Array of user data blobs for users who have ownership rights on the category.
moderators: Array of user data blobs for users who can moderate the category.
goals: Array of strings naming the active goals in this category.
current_races: Array of races that are currently open or in progress, in summary form. Each race's information is the same as what's given in the all races endpoint, excluding the category field.
emotes: Object mapping emote names to their image URLs, e.g. {"PogChamp":"https://racetime.gg/media/PogChamp.png"}.
Past category races
URL: https://racetime.gg/<category>/races/data

Parameters:

show_entrants: If set to true, yes or 1, include entrant data for each race returned.
page: Set to a positive integer (starting from 1) to retrieve paginated data.
per_page: Set to an integer between 10 and 100 (default 10) to change how many races are returned per page.
Returns a list of all completed (finished and cancelled) races in a category. This list is paginated, and sorted by each race's completion time (the ended_at field), most recent first. 10 races are returned per page.

By default each race has the same data as the all races endpoint, excluding the category field. If you enable show_entrants, the races will additionally list entrant data in the same format used by the race detail endpoint.

Category leaderboards
URL: https://racetime.gg/<category>/leaderboards/data

Provides category leaderboard data.

Field breakdown
Each leaderboard in the leaderboards array has the following fields:

goal: String name of the goal, e.g. "Beat the game".
num_ranked: Total number of ranked participants for this leaderboard.
rankings: An ordered array of participants, starting from the highest ranked to the lowest. Contains:
user: User data blob.
place: The user's ranking as an integer, counting from 1 onward.
place_ordinal: Same as place except it's an ordinal string, i.e. "1st", "2nd", "3rd", and so on.
score: The user's calculated score, always a positive integer. Users who have never played start with a score of 833.
times_raced: The number of times the user has entered into recorded races for this goal (including DNF/DQ results).
Race detail
URL: https://racetime.gg/<category>/<race>/data

Replace with the category slug, e.g. For OoTR use ootr, and with the race room identifier, e.g. social-kirby-4429. Typically you'll determine the race URL by first retrieving data from one of the other endpoints, which will point you directly to this URL.

This endpoint covers everything you might want to know about a race. All the data shown on the race page, except for chat messages, is provided. A full breakdown of entrants is also here, which is sorted by race status and finish position, as appropriate.

Note: For races in progress, the timer is not part of the API response, since the API is cached it would be impossible to update this in real-time. To work out the timer, you should use the value of started_at (and the X-Date-Exact header to account for clock sync inaccuracy - see above) and ended_at (if the race has concluded).

Field breakdown
version: Integer indicating the data's version. This is incremented whenever a race changes.
name: The race's unique name, based on the category and a randomly assigned slug.
category: An object giving brief information about the category. Contains:
name: The name of the category, e.g. "Super Mario 64".
short_name: An abbreviated name, e.g. "OoTR".
slug: Unique category slug (part of the URL).
url: URL for the main category page.
data_url: URL for the category data endpoint, which you can use to obtain more detailed category information.
status: An object giving brief information about the race's status. Contains three keys:
value: A machine-parsable status text. Possible values are:
open
invitational
pending
in_progress
finished
cancelled
partitioned (only for ladder 1v1 races)
verbose_value: A user-parsable status text, e.g. "In progress".
help_text: Describes the status, e.g. "Race is in progress".
url: URL for the main race page.
data_url: URL for the race data endpoint, which you can use to obtain more detailed race information.
websocket_url: URL of the race WebSocket, used by the frontend for chat messages and real-time updates.
websocket_bot_url: URL of the WebSocket for category bots.
websocket_oauth_url: URL of the WebSocket for OAuth2-authenticated user connections. Used by third-party applications.
goal: An object describing the race goal. Contains:
name: A string value indicating the current goal.
custom: A boolean indicating if the goal name was custom, or one of the pre-set category goals.
info: String containing additional information for race entrants. This is a combination of info_bot and info_user (in that order).
info_bot: String containing additional information for race entrants, as set by race bots.
info_user: String containing additional information for race entrants, as set by the monitors.
entrants_count: Total number of entrants in this race (including DQ/forfeits).
entrants_count_finished: Total number of entrants that have finished (not counting DQ/forfeits).
entrants_count_inactive: Total number of entrants that have been DQed or forfieted.
entrants: The entrants list, given as an array. Ordered by race status, then by finish position (if applicable), then by score (if available), and finally by name. See below for a breakdown of entrant data blobs.
opened_at: Date/time when the race was first created (ISO 8601 date).
start_delay: The time allocated for the countdown, i.e. time lapse between the last entrant readying up and the race starting (ISO 8601 duration).
started_at: Date/time when the race started, or null if it hasn't started yet (ISO 8601 date).
ended_at: Date/time when the race ended, or null if it hasn't finished yet (ISO 8601 date).
cancelled_at: Date/time when the race was cancelled, or null if it hasn't been cancelled (ISO 8601 date).
Note: a race may be cancelled at any point before it's finished. If it was cancelled before the race started, started_at and ended_at will not be set. If it was cancelled after the race started, started_at will be set and ended_at will be equal to cancelled_at.
ranked: Boolean indicating if the race result can be recorded when the race is concluded.
unlisted: Boolean indicating an unlisted race (hidden from category view except for moderators).
time_limit: The maximum amount of time the race may be in progress for once it starts (ISO 8601 duration).
time_limit_auto_complete: Boolean indicating race behaviour if the time limit is reached. If false, the race will be cancelled. If true, the race will be completed (and may still be recorded).
require_even_teams: Boolean indicating if teams must be balanced for the race to start.
streaming_required: Boolean indiciating if entrants are required to stream in this race.
auto_start: Boolean indicating if the race will start automatically when all entrants are ready.
opened_by: User data blob for the user who opened the race room, or null if the room was opened by a bot. If present, this user is always a race monitor.
monitors: Array of user data blobs for race monitors (in addition to the room opener) in this race.
recordable: Boolean indicating a race can be recorded once it's finished. A moderator may still opt to not record the race.
recorded: Boolean indicating if the race has been recorded by a moderator.
recorded_by: User data blob of the moderator who recorded this race.
disqualify_unready: Boolean indicating if users will be disqualified if they are entered into the race but do not ready up (only applies to 1v1 ladder races)
allow_comments: Boolean indicating if users may add a glib remark after they finish racing.
hide_comments: Boolean indicating if entrant comments will be hidden until the race is finished (or cancelled).
hide_entrants: Boolean indiciating if entrant identities are currently anonymised.
chat_restricted: Boolean indicating if chat restrictions are currently in place (due to allow_prerace_chat or other settings).
allow_prerace_chat: Boolean indicating if users may chat while the race is preparing (does not affect monitors or moderators).
allow_midrace_chat: Boolean indicating if users may chat while the race is in progress (does not affect monitors or moderators).
allow_non_entrant_chat: Boolean indicating if users who have not entered the race may chat while the race is in progress (does not affect moderators).
chat_message_delay: Length of time where chat messages will only appear for race monitors (ISO 8601 duration).
bot_meta: Object containing custom data (see the setmeta command for further details).
Field breakdown (entrant)
Each array item in the entrants list is broken down as follows:

user: User data blob for this entrant.
If hide_entrants is enabled, this data will be anonymised.
status: An object describing the entrant's current status. Contains:
value: A machine-parsable status text. Possible values are:
requested (requested to join)
invited (invited to join)
declined (declined invitation)
partitioned (moved to a 1v1 race room, only for 1v1 ladder races)
ready
not_ready
in_progress
done
dnf (did not finish, i.e. forfeited)
dq (disqualified)
verbose_value: A user-parsable status text, e.g. "In progress".
help_text: Describes the status, e.g. "Did not finish the race.".
finish_time: The user's final finish time, or null if they've not finished (ISO 8601 duration).
finished_at: The date/time when the user finished, or null if they've not finished (ISO 8601 date).
place: Integer indicating what position the user finished in.
place_ordinal: String ordinal version of place, e.g. "3rd".
score: Integer amount of points earned by this entrant on the relevant leaderboard. Note that this is not the entrant's current score (unless the race is in progress), it is the score they had when they entered the race, not after.
score_change Integer amount of points gained/lost as a result of this race, or null (not zero!) if race is not recorded.
comment: A string containing a pithy comeback supplied by the user post-race, or null if they have no comment. If hide_comments is true and the race has not concluded, this field is always null.
has_comment: A boolean indicating if the entrant has made a comment. This field is unaffected by the hide_comments setting.
stream_live: Boolean indicating if the user's stream is currently live. This is updated in real-time while a race is in progress, but once an entrant has finished, forfeited or been disqualified it will not be updated.
stream_override: Boolean indicating if a moderator overrode the streaming requirement for this race entrant, allowing them to ready up without their stream being online.
Past user races
URL: https://racetime.gg/user/<user>/races/data

Parameters:

show_entrants: If set to true, yes or 1, include entrant data for each race returned.
page: Set to a positive integer (starting from 1) to retrieve paginated data.
per_page: Set to an integer between 10 and 100 (default 10) to change how many races are returned per page.
Returns a list of all finished (but not cancelled) races that a user has entered. This list is paginated, and sorted by each race's completion time (the ended_at field), most recent first. 10 races are returned per page.

This endpoint behaves similarly to the past category races endpoint. Each race has the same data as the all races endpoint, unless you enable show_entrants. If enabled, the races will additionally list entrant data in the same format used by the race detail endpoint.

User search
URL: https://racetime.gg/user/search

Parameters:

name: Match users whose name starts with the given string (case insensitive).
discriminator: Match users with the given discriminator string (should be a set of four digits, e.g. '0844'). Exact match only.
Alternate parameters:

term: Can be a name, partial name, or a name and discriminator (given in the form 'Name#1234'). Match users with the given data.
Returns an array of matching user data blobs.
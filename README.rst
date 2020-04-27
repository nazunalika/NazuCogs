NazuCogs
^^^^^^^^

Cogs that I built that served a very particular need.

.. contents::

Information
-----------

**Note:** I am not responsible for strange Red Bot behavior while using these plugins. If you have any issues or want a new feature or improvements, open an issue. I do accept PR's if the fixes or changes are valid.

Cogs
----

chanfeeder
++++++++++

This cog will assist in following a 4chan thread on any board. It will check every 60 seconds or so for new changes and post the change to a designated channel.

+----------+-------------------------------------+-------------------+
| subcmd   | acceptable arguments                | notes             |
+==========+=====================================+===================+
| addfeed  | <name> <url> [channel]              | URL must be https |
+----------+-------------------------------------+-------------------+
| remove   | <name>                              |                   |
+----------+-------------------------------------+-------------------+
| list     | (no arguments)                      | current channel   |
+----------+-------------------------------------+-------------------+
| embed    | <name> True/False/Default [channel] | Fancy vs Regular  |
+----------+-------------------------------------+-------------------+
| force    | <name> [channel]                    | Show last post    |
+----------+-------------------------------------+-------------------+

**Todo**

* Provide a method to allow templates to change from default embed look.
* Provide a regex that catches new thread references so it doesn't get mistaken as a reply to a previous comment
* Provide a regex that catches board cross-linking, including cross-linking to a board *and* thread
* Provide the post number relative to the post (eg, is it the first reply, second reply, etc)


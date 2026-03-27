Future Implementation Ideas

- Handle buildings that aren't in the default base game the old way
- Make compatible with Glorp UI
- Add button to enable/disable auto-expand for every building in a location
- Modify the auto-expand button for buildings so holding control and clicking will toggle every building of that type in the province. Useful when you know you want to say auto-expand masons in a province that produces stone
- See if we can add a filter so you can filter irrigation and farming villages by RGO in the location
- Add automatic closing of buildings using construction goods to reduce demand and lower price
- Find fix for not being able to remove enabled auto-expands and RGOs when tag switching via the world map so we can cleanup variables when you change tags in-game
- Add additional buildings to the auto-build list based on country/religion/culture
- Use on_location_changed_owner to remove auto-expand variables when a location changes hands
  #root= location, scope:loser = previous owner, scope:winner = new owner
  on_location_changed_owner
- Pull together the disparate auto stuff into a single monthly check or daily check rather than having 3 separate on actions
- Fix conservative check for enough construction goods with some testing around when construction stalls and when if it slows down when it's not stalled but there's a negative balance of a good
- Add ability to sort by buildings and locations that have auto-expand enabled
- Fix auto-build so we skip locations that the player owns that are currently occupied by the enemy in a war (not bug since game will prevent the building from making progress but a waste of money since it won't build)
- Add notification for when you don't have a cheap enough price in a market for max discount


Completed Ideas:
- Add option to toggle auto-expand in the buildings filtered view so it's easy to do mass auto-expand in large countries
- Add a setting for minimum control before auto-build occurs
- Add a setting for amount of overbuild allowed in a location by percent of extra cost (maybe just how many extra levels over cap)
- Add a setting to set minimum building discount before auto-expanding
- Increase max minimum gold to save for late game
- Add check to make sure there's enough goods to auto-expand an RGO




No longer considered:
- Add a setting to toggle prioritizing locations by max control # don't need and no one has requested or even asked how it works so no need for a setting to change

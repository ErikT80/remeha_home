# Remeha Home integration for Home Assistant
This integration lets you control your Remeha Home thermostats from Home Assistant.

**Before using this integration, make sure you have set up your thermostat in the [Remeha Home](https://play.google.com/store/apps/details?id=com.bdrthermea.application.remeha) app.**
If you are unable to use the Remeha Home app for your thermostat, this integration will not work.

There have been reports by users that this integration will also work for Baxi, De Dietrich, and Brötje systems (and possibly other BDR Thermea products).
You can simply log in using the credentials that you would use in the respective apps.

## Current features
- All climate zones are exposed as [climate](https://www.home-assistant.io/integrations/climate/) entities with:
    - The following modes:
        - Auto mode: the thermostat will follow the clock program.
        If the target temperature is changed, it will temporarily override the clock program until the next target temperature change in the schedule.
        - Heat mode: the thermostat will be set to manual mode and continuously hold the set temperature.
        For heat pumps this mode is shown as Heat/Cool.
        - Off mode: the thermostat is set to frost protection.
        A temperature can't be set in this mode: change the mode first, or pass `hvac_mode` together with the temperature in `climate.set_temperature`.
    - Three presets for the three clock programs available in the Remeha Home app.
    When a preset is selected, the integration will automatically switch the climate zone to auto mode to make sure the preset is applied.
    In heat (or heat/cool) mode the preset shows Manual, in off mode Frost protection.
- Each climate zone also exposes the following sensors/switches/numbers:
    - The room temperature
    - The status (e.g. heating or idle), plus a binary status sensor (disabled by default)
    - The next schedule setpoint
    - The time at which the next schedule setpoint gets activated
    - The current schedule setpoint
    - Switch to control fireplace mode
    - The heating curve slope and base setpoint (if supported by the appliance)
    - The temperatures of the heating activities used in the clock programs
- Each hot water zone exposes:
    - The water temperature (if reported by the appliance)
    - The current activity (Comfort or Eco)
    - The status, plus a binary status sensor (both disabled by default)
    - The time at which the next schedule setpoint gets activated
    - Water heater control with modes (Schedule, Comfort, Eco) and target temperature, the same modes as in the Remeha Home app.
    The target temperature is the setpoint of the active activity, Comfort or Eco.
    - Setpoint numbers for the Comfort and Eco temperatures. The Eco temperature is only available for a hot water tank: a combi boiler doesn't keep water warm in Eco, so Remeha has no Eco temperature for it.
    - A Boost switch to start and stop boost (if supported by the appliance). Remeha only accepts boost while the zone is in Schedule mode, so the switch gives an error in other modes. Stopping boost returns the zone to Schedule. While boosting, the water heater shows the Boost mode and its `remaining_boost_time` attribute shows how long it still runs.
- Each appliance (boiler or heat pump) exposes the following sensors:
    - The water pressure
    - The thermal mode (e.g. heating, cooling or idle)
    - The outdoor temperature measured by the appliance, and the outdoor temperature from the Remeha cloud (disabled by default)
    - Holiday mode (binary sensor), with the scheduled `start_date` and `end_date` as attributes. The `remeha_home.set_holiday_mode` and `remeha_home.cancel_holiday_mode` actions schedule and cancel it, like the holiday mode in the Remeha Home app. Remeha keeps whole days only, and the appliance lowers heating and hot water to its holiday setpoints in this period. The end date is the day you come back: from midnight on that day, the appliance heats normally again.
    - The energy consumed and delivered today for heating, hot water and cooling, for the appliance as a whole (disabled by default)
- Each producer (heat source) of an appliance, such as the heat pump and the gas boiler of a hybrid system, is a separate device with:
    - For heat pumps (and other electric producers): the energy consumed and delivered today for heating, hot water and cooling, and the seasonal efficiency
    - For gas boilers: the gas used today for heating and hot water, in m³

    The energy values come from Remeha and are updated every 15 minutes. Remeha only reports them with a delay of about 2 hours, and right after midnight the values of the new day start at 0.
    On hybrid setups where the existing gas boiler heats the hot water, such as the Remeha Elga Ace, Remeha does not report the gas used for hot water, so that sensor stays at 0.

## Installation

### Install with HACS (recommended)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=msvisser&repository=remeha_home&category=integration)

Do you have [HACS](https://hacs.xyz/) installed?
Click the button or follow the instructions.
1. Search integrations for **Remeha Home**
1. Click `Install`
1. Restart Home Assistant
1. See [Setup](#setup)

### Install manually

1. Install this platform by creating a `custom_components` folder in the same folder as your configuration.yaml, if it doesn't already exist.
2. Create another folder `remeha_home` in the `custom_components` folder. Copy all files from `custom_components/remeha_home` into the `remeha_home` folder.

## Setup
1. In Home Assistant click on `Settings`
1. Click on `Devices & Services`
1. Click on `+ Add integration`
1. Search for and select `Remeha Home`
1. Enter your email address and password
1. Click "Next"
1. Enjoy

## API documentation
For information on the Remeha Home API see [API documentation](documentation/api.md).

-- osm2pgsql flex style for TrackWild
-- Filters features relevant to wildlife encounter risk

local tables = {}

tables.osm_roads = osm2pgsql.define_way_table('osm_roads', {
    { column = 'highway', type = 'text' },
    { column = 'name', type = 'text' },
    { column = 'geometry', type = 'geometry', not_null = true },
})

tables.osm_areas = osm2pgsql.define_area_table('osm_areas', {
    { column = 'feature_key', type = 'text' },
    { column = 'feature_value', type = 'text' },
    { column = 'name', type = 'text' },
    { column = 'geometry', type = 'geometry', not_null = true },
})

tables.osm_settlements = osm2pgsql.define_node_table('osm_settlements', {
    { column = 'place', type = 'text' },
    { column = 'name', type = 'text' },
    { column = 'population', type = 'text' },
    { column = 'geometry', type = 'geometry', not_null = true },
})

tables.osm_railways = osm2pgsql.define_way_table('osm_railways', {
    { column = 'railway', type = 'text' },
    { column = 'name', type = 'text' },
    { column = 'geometry', type = 'geometry', not_null = true },
})

-- Roads we care about
local highway_values = {
    motorway = true, trunk = true, primary = true,
    secondary = true, tertiary = true, residential = true,
    track = true, path = true, unclassified = true,
    living_street = true, service = true,
    footway = true, bridleway = true, cycleway = true, pedestrian = true,
}

-- Railways we care about
local railway_values = {
    rail = true, light_rail = true, subway = true,
    narrow_gauge = true, tram = true,
    abandoned = true,
}

-- Area features: positive (wildlife) and negative (infrastructure)
local area_keys = {
    landuse = { forest = true, meadow = true, grassland = true, farmland = true,
                residential = true, industrial = true, commercial = true,
                town = true, village = true, barren = true,
                orchard = true, vineyard = true, plant_nursery = true, cemetery = true,
                tundra = true, reedbed = true, retail = true, construction = true,
                allotments = true, farmyard = true, landfill = true, quarry = true,
                military = true, railway = true },
    natural = { wood = true, scrub = true, wetland = true, heath = true,
                water = true, glacier = true, bare_rock = true, scree = true, sand = true,
                tundra = true, marsh = true, bog = true, fen = true, beach = true, dune = true,
                tree_row = true, shrubbery = true, moor = true, cliff = true,
                rock = true, stones = true, ridge = true, arete = true,
                fell = true },
    leisure = { nature_reserve = true, national_park = true, park = true,
                garden = true, common = true },
    military = { danger_area = true, barracks = true, airfield = true,
                 training_area = true, naval_base = true, obstacle_course = true,
                 range = true, bunker = true, checkpoint = true },
    aeroway = { aerodrome = true, runway = true, taxiway = true, helipad = true,
                apron = true, hangar = true, terminal = true },
    power = { plant = true, substation = true, generator = true, line = true },
    amenity = { hospital = true, school = true, university = true,
                prison = true, police = true, fire_station = true,
                bus_station = true, ferry_terminal = true,
                marketplace = true, parking = true },
    tourism = { caravan_site = true, camp_site = true },
    boundary = { protected_area = true },
}

-- Settlement types
local place_values = {
    city = true, town = true, village = true,
    hamlet = true, isolated_dwelling = true,
    suburb = true, neighbourhood = true,
}

-- Ways: try as road first, then railway, then as area if closed
function osm2pgsql.process_way(object)
    local highway = object:grab_tag('highway')
    if highway and highway_values[highway] then
        tables.osm_roads:insert({
            osm_id = object.id,
            highway = highway,
            name = object:grab_tag('name'),
            geometry = object:as_linestring(),
        })
        return
    end

    local railway = object:grab_tag('railway')
    if railway and railway_values[railway] then
        tables.osm_railways:insert({
            osm_id = object.id,
            railway = railway,
            name = object:grab_tag('name'),
            geometry = object:as_linestring(),
        })
        return
    end

    if object.is_closed then
        for key, values in pairs(area_keys) do
            local val = object:grab_tag(key)
            if val and values[val] then
                tables.osm_areas:insert({
                    osm_id = object.id,
                    feature_key = key,
                    feature_value = val,
                    name = object:grab_tag('name'),
                    geometry = object:as_polygon(),
                })
                return
            end
        end
        -- Catch-all for buildings (closed ways without any other area tag)
        local building = object:grab_tag('building')
        if building then
            tables.osm_areas:insert({
                osm_id = object.id,
                feature_key = 'building',
                feature_value = 'yes',
                name = object:grab_tag('name'),
                geometry = object:as_polygon(),
            })
        end
    end
end

function osm2pgsql.process_relation(object)
    if object.tags.type ~= 'multipolygon' then return end

    for key, values in pairs(area_keys) do
        local val = object:grab_tag(key)
        if val and values[val] then
            tables.osm_areas:insert({
                osm_id = object.id,
                feature_key = key,
                feature_value = val,
                name = object:grab_tag('name'),
                geometry = object:as_multipolygon(),
            })
            return
        end
    end
end

function osm2pgsql.process_node(object)
    local place = object:grab_tag('place')
    if place and place_values[place] then
        tables.osm_settlements:insert({
            osm_id = object.id,
            place = place,
            name = object:grab_tag('name'),
            population = object:grab_tag('population'),
            geometry = object:as_point(),
        })
    end
end

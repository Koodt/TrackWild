-- osm2pgsql flex style for TrackWild
-- Filters only features relevant to wildlife encounter risk

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

-- Roads we care about
local highway_values = {
    motorway = true, trunk = true, primary = true,
    secondary = true, tertiary = true, residential = true,
    track = true, path = true, unclassified = true,
    living_street = true, service = true,
}

-- Area features we care about
local area_keys = {
    landuse = { forest = true, meadow = true, grassland = true, farmland = true },
    natural = { wood = true, scrub = true, wetland = true, heath = true },
    leisure = { nature_reserve = true, national_park = true },
}

-- Settlement types
local place_values = {
    city = true, town = true, village = true,
    hamlet = true, isolated_dwelling = true,
}

-- Ways: try as road first, then as area if closed
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

    -- Closed ways as areas (landuse, natural, leisure)
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
    end
end

-- Relations: only multipolygon areas
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

import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

const TIME_PERIODS = [
  { id: 'night', label: 'Ночь', icon: '🌙' },
  { id: 'morning', label: 'Утро', icon: '🌅' },
  { id: 'day', label: 'День', icon: '☀️' },
  { id: 'evening', label: 'Вечер', icon: '🌇' },
]

export default function App() {
  const mapContainer = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [selectedTime, setSelectedTime] = useState('day')

  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: '/style.json',
      center: [31.2, 69.7],
      zoom: 5,
    })

    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    mapRef.current = map

    map.on('load', () => {
      map.addSource('heatmap', {
        type: 'raster',
        tiles: [
          `/v1/tiles/${selectedTime}/{z}/{x}/{y}.png`,
        ],
        tileSize: 256,
        minzoom: 3,
        maxzoom: 14,
      })

      map.addLayer({
        id: 'heatmap-layer',
        type: 'raster',
        source: 'heatmap',
        paint: {
          'raster-opacity': 0.6,
        },
      })
    })

    return () => map.remove()
  }, [])

  useEffect(() => {
    if (!mapRef.current) return
    const map = mapRef.current

    if (map.isStyleLoaded()) {
      const source = map.getSource('heatmap')
      if (source) {
        const rasterSource = source as maplibregl.RasterTileSource
        rasterSource.setTiles([
          `/v1/tiles/${selectedTime}/{z}/{x}/{y}.png`,
        ])
      }
    }
  }, [selectedTime])

  // Periodically refresh the heatmap layer to pick up newly generated tiles.
  // Pending tiles are returned as transparent PNGs with Cache-Control: no-cache,
  // so MapLibre will re-request them on invalidation.
  useEffect(() => {
    const interval = setInterval(() => {
      if (mapRef.current?.isStyleLoaded()) {
        const source = mapRef.current.getSource('heatmap') as maplibregl.RasterTileSource | undefined
        if (source) {
          // Force MapLibre to re-fetch all visible tiles
          const tiles = source.tiles
          source.setTiles([...tiles])
        }
      }
    }, 10000) // every 10 seconds

    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{ width: '100vw', height: '100vh', position: 'relative' }}>
      <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />

      {/* Time of day selector */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 12,
          zIndex: 10,
          display: 'flex',
          gap: 2,
          background: 'rgba(0,0,0,0.75)',
          borderRadius: 10,
          padding: 4,
        }}
      >
        {TIME_PERIODS.map((period) => (
          <button
            key={period.id}
            onClick={() => setSelectedTime(period.id)}
            title={period.label}
            style={{
              width: 44,
              height: 44,
              border: selectedTime === period.id ? '2px solid #4fc3f7' : '2px solid transparent',
              borderRadius: 8,
              background: selectedTime === period.id ? '#1565c0' : 'transparent',
              color: '#fff',
              fontSize: 20,
              cursor: 'pointer',
              padding: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 1,
              transition: 'all 0.15s ease',
            }}
          >
            <span>{period.icon}</span>
            <span style={{ fontSize: 8, lineHeight: 1 }}>{period.label}</span>
          </button>
        ))}
      </div>

      {/* Legend */}
      <div
        style={{
          position: 'absolute',
          bottom: 20,
          right: 12,
          zIndex: 10,
          background: 'rgba(0,0,0,0.75)',
          borderRadius: 10,
          padding: '12px 16px',
          color: '#fff',
          fontSize: 12,
        }}
      >
        <div style={{ marginBottom: 6, fontWeight: 'bold' }}>Риск встречи</div>
        <div
          style={{
            width: 120,
            height: 14,
            background: 'linear-gradient(to right, #00ff00, #ffff00, #ff0000)',
            borderRadius: 3,
          }}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
          <span>Низкий</span>
          <span>Высокий</span>
        </div>
      </div>
    </div>
  )
}

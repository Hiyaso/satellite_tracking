from flask import Flask, jsonify, request
from flask_cors import CORS
from skyfield.api import load, wgs84, EarthSatellite
from datetime import datetime, timezone

from satellite_tracking.src.acu import calculate_az_el_from_tle

app = Flask(__name__)
CORS(app)  

ts = load.timescale()
station = wgs84.latlon(+25.0330, +121.5654) # 台北地面站

# 變更為 POST 方法，以接收前端傳來的 JSON (包含 TLE 資料)
@app.route('/api/next-pass', methods=['POST'])
def get_next_pass():
    try:
        data = request.get_json()
        sat_name = data.get('name', 'Custom Satellite')
        line1 = data.get('line1')
        line2 = data.get('line2')

        if not line1 or not line2:
            return jsonify({"status": "error", "message": "Missing TLE data"}), 400

        # 建立動態衛星物件
        satellite = EarthSatellite(line1, line2, sat_name, ts)
        
        t0 = ts.now()
        t1 = ts.from_datetime(datetime.now(timezone.utc).replace(day=datetime.now().day + 3))
        
        times, events = satellite.find_events(station, t0, t1, altitude_degrees=10.0)
        
        pass_list = []
        current_pass = {}
        
        for ti, event in zip(times, events):
            time_str = ti.utc_datetime().strftime('%Y-%m-%d %H:%M:%S UTC')
            
            if event == 0:
                current_pass['rise'] = time_str
            elif event == 1:
                current_pass['culminate'] = time_str
            elif event == 2:
                current_pass['set'] = time_str
                pass_list.append(current_pass)
                current_pass = {}

        return jsonify({
            "status": "success",
            "satellite": sat_name,
            "passes": pass_list
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/az-el', methods=['POST'])
def get_az_el():
    try:
        data = request.get_json()
        line1 = data.get('line1')
        line2 = data.get('line2')

        if not line1 or not line2:
            return jsonify({"status": "error", "message": "Missing TLE data"}), 400

        lat = data.get('lat', 25.0330)
        lon = data.get('lon', 121.5654)
        elev_m = data.get('elevation_m', 0.0)
        when_value = data.get('when')
        when = None

        if when_value:
            when = datetime.fromisoformat(when_value.replace('Z', '+00:00'))

        result = calculate_az_el_from_tle(line1, line2, lat, lon, elev_m, when)
        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # 請保持你目前成功運作的 Port (例如 8000 或 9000)
    app.run(host='0.0.0.0', port=8000)
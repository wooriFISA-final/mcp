# mcp_server/tools/weather_tool.py
def weather_tool(city: str):
    """간단한 날씨 도구 (샘플)"""
    fake_weather = {
        "서울": 18,
        "부산": 22,
        "대전": 20
    }
    temp = fake_weather.get(city, 25)
    return {"city": city, "temp": temp}

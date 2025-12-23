import aos
import server

client = aos.init_client(is_asycn=False, endpoint='custom')
res = server.submit_task(client, "100%%20MDRT%20DAY%20BY%20V%20TANYA%20FAN%20&%20GO%20LEONA%20LU-111825.mp4")

print(res)
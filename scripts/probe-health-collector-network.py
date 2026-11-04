"""Report proxy presence and a bounded public RSS request; never print values."""
import hashlib,json,os
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from dotenv import dotenv_values

keys={'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'}
configs={}
for label,path in [('producer',Path('/opt/signaldesk/.env')),('task',Path('/opt/signaldesk-task/.env'))]:
 values=dotenv_values(path,interpolate=False)
 configs[label]={key.upper():str(value) for key,value in values.items() if key.upper() in keys and value}
result={'read_only':True,'private_configuration_exported':False,'values_or_credentials_printed':False,'messages_sent':0,'business_database_written':False,'proxy_presence':{label:{key:key in values for key in sorted(keys)} for label,values in configs.items()},'network':{}}
proxy=configs['producer'].get('HTTPS_PROXY') or configs['producer'].get('ALL_PROXY') or configs['producer'].get('HTTP_PROXY')
if proxy:
 parsed=urlsplit(proxy)
 assert parsed.scheme in {'http','https','socks5','socks5h'} and parsed.hostname
 result['producer_proxy_metadata']={'scheme':parsed.scheme,'hostname_sha256':hashlib.sha256(parsed.hostname.encode()).hexdigest(),'credentials_present':bool(parsed.username or parsed.password)}
for label,value in [('direct',None)]+([('producer_proxy',proxy)] if proxy else []):
 try:
  with httpx.Client(proxy=value,trust_env=False,timeout=httpx.Timeout(12,connect=6),follow_redirects=False) as client:
   with client.stream('GET','https://news.google.com/rss/search',params={'q':'healthcare','hl':'en-US','gl':'US','ceid':'US:en'},headers={'User-Agent':'Mozilla/5.0','Accept':'application/rss+xml, application/xml'}) as response:
    first=next(response.iter_bytes(),b'')
    result['network'][label]={'status':response.status_code,'received_bytes':min(len(first),256),'content_type':response.headers.get('content-type','').split(';')[0]}
 except Exception as error:result['network'][label]={'error_type':type(error).__name__}
print(json.dumps(result))

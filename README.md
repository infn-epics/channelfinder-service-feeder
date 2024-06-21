# Channelfinder Service Feeder

It retrieve information from a directory where there are dumps of PVs.
It initialize the *channelFinder phoebus service*


## Install

```
python -mvenv venv
source venv/bin/activate
pip install -f requirements.txt
```


## Example

Execute:
```
python cfeeder.py <config_dir> <cf_service_url> <username> <password>

```
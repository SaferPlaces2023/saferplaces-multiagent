# SaferPlaces AI Agent

# ------ Sviluppato ------

## Agente:

### Saferplaces API Tools
- digital twin
- safer rain
- safer buildings

### Safercast API Tools
- dpc retriever
- icon 2i retriever

### Other tools
- geospatial operation tool ("somma il layer 1 al layer 2", "crea un layer con la bbox di roma", "apri la feature collection e raggruppa per property" ...)

## Gestione file (layer creati dai tool o caricati)
- bucket s3 s3://saferplaces.co/SaferPlaces-Agent/dev/user=<USER_ID>/project=<PROJECT_ID>/

## Interfaccia (webapp)
- login user + project id
- pannello utente (thread, userid, e progetti)
- pannello layers (descrizione e controllo layer)
- visualizzazione layer temporali (dpc, icon) - ok in generale, da rendere fluida ogni tanto bug
- visualizzazione 3d edifici e terreno (con dem).

# ------ Next ------

## Altri tool
- untrim
- radarmeteo
- meteoblue
- eedem dem only
- altri algoritmi che abbiamo 

## WebApp utils
- switch tra progetti
- zoom to layer
- download layer
- export project
- impostare simbologie layer (impo)

## Disegno geometrie
- disegno bbox + "usala per fare digital twin"
- disegno di feature per usarle con l'agente ("crop del raster su questo poligono")
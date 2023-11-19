from PyInstaller.utils.hooks import collect_data_files

# Pyinstaller Hook that takes care of cacert.pem missing from the path when building
datas = collect_data_files('curl_cffi')

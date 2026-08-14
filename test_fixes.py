import warnings
import pandas as pd
import requests

def test_warning():
    print("Testing warning filter...")
    # Just emit a warning to see if we can catch it
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.warn("Workbook contains no default style, apply openpyxl's default", UserWarning)
    print("Warning filter tested.")

def test_request():
    print("Testing request 404...")
    url = "https://dados.cvm.gov.br/dados/FI/DOC/PERFIL_MENSAL/DADOS/perfil_mensal_fi_202608.csv"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print("Caught 404 specifically!")
        else:
            print(f"Caught other HTTPError: {e}")
    except Exception as e:
        print(f"Caught generic Exception: {e}")

if __name__ == "__main__":
    test_warning()
    test_request()

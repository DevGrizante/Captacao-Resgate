import time
import random
import string
import sys

def main():
    # Define um tempo aleatório entre 5 e 10 minutos (300 a 600 segundos)
    duration = random.randint(300, 600)
    start_time = time.time()
    
    # Lista de prefixos para parecer um log de sistema realista
    prefixes = ["INFO", "DEBUG", "WARN", "SYS", "NET", "PROC", "MEM", "KERNEL"]
    
    print("Iniciando processamento em lote...")
    print(f"Tempo estimado da operação: {duration // 60}m {duration % 60}s\n")
    time.sleep(1)
    
    try:
        while time.time() - start_time < duration:
            prefix = random.choice(prefixes)
            # Gera um identificador hexadecimal aleatório
            rand_hex = ''.join(random.choices(string.hexdigits.upper(), k=8))
            # Gera uma string aleatória (como se fosse dados criptografados ou hashes)
            rand_text = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%^&*()", k=random.randint(40, 80)))
            
            # Print formatado
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{prefix}] [0x{rand_hex}] {rand_text}")
            
            # Pausa aleatória muito curta para criar o efeito de texto passando rápido, mas de forma orgânica
            time.sleep(random.uniform(0.005, 0.05))
            
    except KeyboardInterrupt:
        print("\n[!] Processo interrompido pelo usuário.")
        sys.exit(1)
        
    print("\n[OK] Processamento concluído com sucesso!")

if __name__ == "__main__":
    main()

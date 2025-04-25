#!/usr/bin/env python3
# Script para instalación de Odoo 18.0 en Proxmox LXC con Ubuntu 24.04
import os, sys, json, subprocess, re, time, shutil, glob

# Configuración de colores
C = {
    'R': '\033[0;31m', 'G': '\033[0;32m', 'B': '\033[0;34m', 'Y': '\033[1;33m',
    'C': '\033[0;36m', 'P': '\033[0;35m', 'O': '\033[0;33m', 'N': '\033[0m', 'BOLD': '\033[1m'
}

# Funciones de utilidad
def msg(text, type_='INFO', color='B'): print(f"{C[color]}[{type_}]{C['N']} {text}")
def success(text): msg(text, 'SUCCESS', 'G')
def warning(text): msg(text, 'WARNING', 'Y')
def error(text): print(f"{C['R']}[ERROR]{C['N']} {text}", file=sys.stderr)
def error_exit(text): error(text); sys.exit(1)
def section(title): print(f"\n{C['P']}{C['BOLD']}╔═════════════════════════════════════════════════════════════════╗{C['N']}\n{C['P']}{C['BOLD']}  {title}{C['N']}\n{C['P']}{C['BOLD']}╚═════════════════════════════════════════════════════════════════╝{C['N']}")
def show_item(label, value=""): print(f"  {C['BOLD']}•{C['N']} {label} {C['C']}{value}{C['N']}")
def show_group(title): print(f"{C['BOLD']}{title}:{C['N']}")
def ask(prompt, default, validation=None, error_msg="Valor inválido"):
    while True:
        user_input = input(f"{C['C']}{prompt} [{default}]: {C['N']}").strip() or default
        if validation is None or re.match(validation, user_input): return user_input
        warning(error_msg)
def confirm_action(prompt, default): 
    # Formato de opciones basado en el valor predeterminado (Y/n o y/N)
    options = "Y/n" if default.lower().startswith('y') else "y/N"
    return (input(f"{C['G']}{prompt} ({options}): {C['N']}").strip().lower() or default.lower()).startswith('y')
def run_command(command, exit_on_error=True, show_output=False):
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if show_output: print(result.stdout)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if exit_on_error: error_exit(f"Error: {command}\nSalida: {e.stderr}")
        return None

# Funciones de almacenamiento
def get_storage_data():
    try:
        hostname = run_command("hostname")
        storage_json = run_command(f"pvesh get /nodes/{hostname}/storage --output-format=json")
        return json.loads(storage_json)
    except Exception as e: error_exit(f"Error al obtener datos de almacenamiento: {str(e)}")

def enable_storage_content(storage, content_type, readable_name, storage_data):
    storage_info = next((item for item in storage_data if item['storage'] == storage), None)
    if not storage_info: error_exit(f"Almacenamiento '{storage}' no encontrado")

    content = storage_info.get('content', '')
    content_list = content.split(',') if content else []

    if content_type not in content_list:
        warning(f"El almacenamiento '{storage}' no soporta {readable_name} ({content_type})")
        if confirm_action(f"¿Habilitar soporte para {readable_name}?", "Y"):
            new_content = f"{content},{content_type}" if content else content_type
            run_command(f"pvesh set /storage/{storage} --content '{new_content}'")
            success(f"Soporte para {readable_name} habilitado en '{storage}'")
            return get_storage_data()
        else: error_exit(f"Se requiere soporte para {readable_name}")
    else:
        msg(f"El almacenamiento '{storage}' ya soporta {readable_name}")
        return storage_data

def show_storages(storage_data, storages):
    section("ALMACENAMIENTO DISPONIBLE")
    for index, name in enumerate(storages, 1):
        info = next((item for item in storage_data if item['storage'] == name), {})
        content = info.get('content', '')
        avail, total, used = info.get('avail', 'N/A'), info.get('total', 'N/A'), info.get('used', 'N/A')
        rootdir_support = "SÍ" if "rootdir" in content else "NO"
        vztmpl_support = "SÍ" if "vztmpl" in content else "NO"

        format_size = lambda size: f"{size/1024/1024/1024:.2f} GB" if isinstance(size, (int, float)) else "N/A"
        avail_display, total_display, used_display = format_size(avail), format_size(total), format_size(used)
        used_percent = f"{used*100/total:.2f}%" if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0 else "N/A"

        print(f"  {C['BOLD']}{index}) {name}{C['N']}")
        show_item("Espacio total", total_display)
        show_item("Espacio usado", f"{used_display} ({used_percent})")
        show_item("Espacio disponible", avail_display)
        show_item("Compatible con contenedores", rootdir_support)
        show_item("Compatible con plantillas", vztmpl_support)
        print("")

# Comprobar módulos personalizados
def check_custom_modules():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    modules_dir = os.path.join(script_dir, "modules")
    
    if not os.path.exists(modules_dir):
        msg("No se encontró el directorio 'modules'")
        return [], modules_dir
    
    modules = []
    for item in os.listdir(modules_dir):
        module_path = os.path.join(modules_dir, item)
        if os.path.isdir(module_path) and os.path.exists(os.path.join(module_path, "__manifest__.py")):
            modules.append(item)
    
    return modules, modules_dir

# Crear script de instalación de Odoo
def create_odoo_install_script(odoo_version, db_pass, odoo_user, custom_modules):
    has_custom_modules = len(custom_modules) > 0
    custom_modules_str = ', '.join([f'"{m}"' for m in custom_modules])
    
    addons_path = f'/opt/odoo18/addons,/opt/odoo18/custom_addons' if has_custom_modules else f'/opt/odoo18/addons'

    script_content = f'''#!/bin/bash
# Script de instalación de Odoo {odoo_version} - C3i Servicios Informáticos
info() {{ echo "[INFO] $1"; }}
success() {{ echo "[SUCCESS] $1"; }}
warning() {{ echo "[WARNING] $1"; }}
error() {{ echo "[ERROR] $1"; }}
progress() {{ echo "[PROGRESS] $1"; }}

# Actualizar sistema
info "Actualizando sistema..."
apt-get update && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
success "Sistema actualizado"

# Instalar requisitos
info "Instalando dependencias..."
progress "Instalando paquetes del sistema (1/5)"
apt-get install -y openssh-server fail2ban python3-pip python3-dev libxml2-dev libxslt1-dev zlib1g-dev libsasl2-dev
progress "Instalando bibliotecas de desarrollo (2/5)"
apt-get install -y libldap2-dev build-essential libssl-dev libffi-dev default-libmysqlclient-dev libjpeg-dev libpq-dev
progress "Instalando bibliotecas de procesamiento de imágenes (3/5)"
apt-get install -y libjpeg8-dev liblcms2-dev libblas-dev libatlas-base-dev
progress "Instalando Node.js y npm (4/5)"
apt-get install -y npm git postgresql python3-venv
progress "Configurando fail2ban y Node.js (5/5)"
systemctl enable fail2ban
ln -sf /usr/bin/nodejs /usr/bin/node
npm install -g less less-plugin-clean-css
apt-get install -y node-less
success "Dependencias instaladas"

# Configurar PostgreSQL
info "Configurando PostgreSQL..."
su - postgres -c "createuser --createdb --username postgres --no-createrole --superuser --pwprompt {odoo_user} << EOF
{db_pass}
{db_pass}
EOF"
success "PostgreSQL configurado"

# Crear usuario de Odoo
info "Creando usuario del sistema para Odoo..."
adduser --system --home=/opt/odoo18 --group {odoo_user}
success "Usuario del sistema creado"

# Clonar Odoo
info "Clonando repositorio de Odoo..."
progress "Descargando código fuente de Odoo (esto puede tardar varios minutos)..."
su - {odoo_user} -s /bin/bash -c "git clone https://www.github.com/odoo/odoo --depth 1 --branch {odoo_version} --single-branch ."
success "Repositorio de Odoo clonado"

# Instalar dependencias de Python
info "Instalando dependencias de Python..."
python3 -m venv /opt/odoo18/venv
cd /opt/odoo18/
progress "Instalando requisitos de Python en entorno virtual (esto puede tardar varios minutos)..."
/opt/odoo18/venv/bin/pip install wheel
/opt/odoo18/venv/bin/pip install -r requirements.txt
success "Dependencias de Python instaladas"

# Instalar wkhtmltopdf
info "Instalando wkhtmltopdf..."
apt-get install -y xfonts-75dpi xfonts-base
cd /tmp
progress "Descargando wkhtmltopdf..."
wget -q https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb
progress "Instalando paquete wkhtmltopdf..."
dpkg -i wkhtmltox_0.12.6.1-2.jammy_amd64.deb || apt-get install -f -y
success "wkhtmltopdf instalado"

'''+\
(f'''
# Instalar módulos personalizados
info "Instalando módulos personalizados..."
mkdir -p /opt/odoo18/custom_addons
chown {odoo_user}: /opt/odoo18/custom_addons

# Copiar módulos personalizados a Odoo
for module in {custom_modules_str}; do
    progress "Instalando módulo: $module"
    cp -r /tmp/custom_modules/$module /opt/odoo18/custom_addons/
done

chown -R {odoo_user}: /opt/odoo18/custom_addons/
success "Módulos personalizados instalados"
''' if has_custom_modules else '')+\
f'''

# Configurar Odoo
info "Configurando Odoo..."
mkdir -p /var/log/odoo
progress "Creando archivo de configuración..."
cat > /etc/odoo18.conf << EOL
[options]
; Esta es la contraseña que permite operaciones en la base de datos:
; admin_passwd = admin
db_host = localhost
db_port = 5432
db_user = {odoo_user}
db_password = {db_pass}
addons_path = {addons_path}
default_productivity_apps = True
logfile = /var/log/odoo/odoo18.log
EOL

chown {odoo_user}: /etc/odoo18.conf
chmod 640 /etc/odoo18.conf
chown {odoo_user}:root /var/log/odoo
progress "Creando servicio systemd..."

# Configurar systemd
cat > /etc/systemd/system/odoo18.service << EOL
[Unit]
Description=Odoo {odoo_version}
After=network.target postgresql.service

[Service]
Type=simple
User={odoo_user}
ExecStart=/opt/odoo18/venv/bin/python3 /opt/odoo18/odoo-bin -c /etc/odoo18.conf

[Install]
WantedBy=default.target
EOL

chmod 755 /etc/systemd/system/odoo18.service
progress "Recargando systemd e iniciando servicio de Odoo..."
systemctl daemon-reload
systemctl start odoo18.service
systemctl enable odoo18.service

success "Instalación de Odoo {odoo_version} completada"
'''
    with open('/tmp/odoo_install.sh', 'w') as f:
        f.write(script_content)

# Principal
def main():
    # Pantalla de bienvenida
    os.system('clear')
    print(f"{C['Y']}╔═════════════════════════════════════════════════════════════════╗{C['N']}")
    print(f"{C['Y']}║                   C3i SERVICIOS INFORMÁTICOS                    ║{C['N']}")
    print(f"{C['Y']}║        INSTALADOR AUTOMATIZADO DE ODOO PARA PROXMOX LXC         ║{C['N']}")
    print(f"{C['Y']}╚═════════════════════════════════════════════════════════════════╝{C['N']}\n")
    print(f"{C['C']}Este script instalará Odoo 18.0 en un Proxmox LXC con Ubuntu 24.04{C['N']}\n")

    if not confirm_action("¿Continuar con la instalación?", "Y"):
        print(f"{C['Y']}Instalación cancelada.{C['N']}"); sys.exit(0)
    os.system('clear')

    # Verificar requisitos
    section("VERIFICACIÓN DE REQUISITOS")
    if os.geteuid() != 0: error_exit("Por favor, ejecute como root")

    # Verificar dependencias
    msg("Verificando dependencias...")
    missing_deps = [cmd for cmd in ['pvesh', 'pct', 'curl'] if shutil.which(cmd) is None]
    if missing_deps:
        warning(f"Faltantes: {', '.join(missing_deps)}")
        if confirm_action("¿Instalar dependencias faltantes?", "Y"):
            run_command(f"apt update && apt install -y {' '.join(missing_deps)}")
        else: error_exit("Dependencias requeridas")

    # Verificar módulos personalizados
    section("VERIFICACIÓN DE MÓDULOS PERSONALIZADOS")
    custom_modules, modules_dir = check_custom_modules()
    if custom_modules:
        success(f"Se encontraron {len(custom_modules)} módulos personalizados: {', '.join(custom_modules)}")
    else:
        warning("No se encontraron módulos personalizados en el directorio 'modules'")
        if confirm_action("¿Continuar sin módulos personalizados?", "Y"):
            pass
        else:
            error_exit("Se requieren módulos personalizados para esta instalación")

    # Obtener información de almacenamiento
    msg("Obteniendo almacenamiento disponible...")
    storage_data = get_storage_data()
    storages = [item['storage'] for item in storage_data]
    if not storages: error_exit("No hay almacenamiento disponible")

    show_storages(storage_data, storages)
    storage_num = int(ask("Seleccione almacenamiento (número)", "1", r"^[0-9]+$"))
    if storage_num < 1 or storage_num > len(storages): error_exit("Selección inválida")
    storage = storages[storage_num - 1]
    success(f"Almacenamiento seleccionado: {storage}")

    # Verificar soporte de almacenamiento
    storage_data = enable_storage_content(storage, "rootdir", "contenedores", storage_data)
    storage_data = enable_storage_content(storage, "vztmpl", "plantillas", storage_data)

    # Configuración del contenedor
    section("CONFIGURACIÓN DEL CONTENEDOR")
    config = {
        'vm_id': ask("ID del contenedor (100-999)", "100", r"^[1-9][0-9]{2}$"),
        'hostname': ask("Nombre de host del contenedor", "odoo-server", r"^[a-zA-Z0-9][-a-zA-Z0-9]*$"),
        'password': ask("Contraseña de root del contenedor", "Cambiame123", r"."),
        'memory': ask("RAM (MB, mín 2048)", "4096", r"^[0-9]+$"),
        'disk': ask("Disco (GB, mín 10)", "20", r"^[0-9]+$"),
        'cores': ask("Núcleos de CPU", "2", r"^[0-9]+$"),
    }

    # Configuración de red
    section("CONFIGURACIÓN DE RED")
    use_public_ip = confirm_action("¿Usar IP pública?", "N")

    # Obtener configuración de red predeterminada
    try:
        default_gateway = ""
        default_interface = run_command("ip route | grep default | awk '{print $5}'", exit_on_error=False)
        if default_interface:
            default_cidr = run_command(f"ip -f inet addr show {default_interface} | grep -Po 'inet \\K[\\d.]+/[\\d]+'", exit_on_error=False)
            if default_cidr:
                ip_parts = default_cidr.split('/')[0].split('.')
                default_suggested_ip = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.100"
                default_mask = default_cidr.split('/')[1]
                default_gateway = run_command("ip route | grep default | awk '{print $3}'", exit_on_error=False)
        else:
            default_suggested_ip, default_mask, default_gateway = "192.168.1.100", "24", "192.168.1.1"
    except:
        default_suggested_ip, default_mask, default_gateway = "192.168.1.100", "24", "192.168.1.1"

    if use_public_ip:
        config.update({
            'ip_address': ask("Dirección IP pública", "", r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"),
            'netmask': "32",
            'gateway': ask("Puerta de enlace", default_gateway, r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"),
            'dns_servers': ask("Servidores DNS (separados por coma)", "9.9.9.9,1.1.1.1"),
            'public_ip': True
        })

        # Dirección MAC para IP pública
        while True:
            mac = ask("Dirección MAC para IP pública", "", None)
            if mac and re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', mac):
                config['mac_address'] = mac
                break
            else: error("Se requiere una dirección MAC válida para IP pública")
    else:
        config.update({
            'ip_address': ask("Dirección IP local", default_suggested_ip, r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"),
            'netmask': ask("Máscara de red (CIDR)", default_mask, r"^[0-9]+$"),
            'gateway': ask("Puerta de enlace", default_gateway, r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"),
            'dns_servers': ask("Servidores DNS", "9.9.9.9,1.1.1.1"),
            'public_ip': False,
            'mac_address': None
        })

    # Configuración de Odoo
    section("CONFIGURACIÓN DE ODOO")
    config.update({
        'odoo_version': "18.0",
        'odoo_user': ask("Usuario de base de datos de Odoo", "odoo18", r"^[a-z][a-z0-9_-]*$"),
        'db_password': ask("Contraseña de BD de Odoo", "admin2025", r"."),
    })

    # Resumen
    section("RESUMEN DE CONFIGURACIÓN")
    show_group("Información del Contenedor")
    show_item("ID", config['vm_id'])
    show_item("Nombre de host", config['hostname'])
    show_item("RAM", f"{config['memory']} MB")
    show_item("Disco", f"{config['disk']} GB")
    show_item("Núcleos", config['cores'])
    print("")

    show_group("Configuración de Red")
    if config['public_ip']:
        show_item("IP Pública", f"{config['ip_address']}/32")
        show_item("Dirección MAC", config['mac_address'])
    else:
        show_item("IP Local", f"{config['ip_address']}/{config['netmask']}")
    show_item("Puerta de enlace", config['gateway'])
    show_item("DNS", config['dns_servers'])
    print("")

    show_group("Configuración de Odoo")
    show_item("Versión", config['odoo_version'])
    show_item("Usuario", config['odoo_user'])
    show_item("Contraseña de BD", config['db_password'])
    
    if custom_modules:
        print("")
        show_group("Módulos Personalizados")
        for module in custom_modules:
            show_item("Módulo", module)

    if not confirm_action("\n¿Continuar con la instalación?", "Y"):
        msg("Instalación cancelada"); sys.exit(0)

    # Crear contenedor
    section("CREACIÓN DEL CONTENEDOR")
    msg("Creando contenedor LXC...")
    template = "ubuntu-24.04-standard_24.04-2_amd64.tar.zst"

    hostname_cmd = run_command("hostname")

    template_content_json = run_command(f"pvesh get /nodes/{hostname_cmd}/storage/{storage}/content --output-format=json")
    template_content = json.loads(template_content_json)
    template_exists = any(item.get('volid', '').endswith(template) for item in template_content)

    if not template_exists:
        msg("Descargando plantilla de Ubuntu 24.04...")
        run_command("pveam update")
        run_command(f"pveam download {storage} {template}")

    # Comando para crear contenedor
    create_cmd = (
        f"pct create {config['vm_id']} {storage}:vztmpl/{template} "
        f"-hostname {config['hostname']} "
        f"-password {config['password']} "
        f"-ostype ubuntu "
        f"-rootfs {storage}:{config['disk']} "
        f"-memory {config['memory']} "
        f"-cores {config['cores']} "
    )

    # Configuración de red
    if config['public_ip']:
        create_cmd += f"-net0 name=eth0,bridge=vmbr0,ip={config['ip_address']}/{config['netmask']},gw={config['gateway']},hwaddr={config['mac_address']} "
    else:
        create_cmd += f"-net0 name=eth0,bridge=vmbr0,ip={config['ip_address']}/{config['netmask']},gw={config['gateway']} "

    create_cmd += f"-onboot 1 -start 1 -unprivileged 1 -features nesting=1 -nameserver '{config['dns_servers']}'"

    run_command(create_cmd)
    success("Contenedor creado")

    # Configurar para IP pública /32
    if config['public_ip']:
        msg("Configurando rutas para IP pública...")
        netplan_config = f"""# Configuración de red para IP pública
network:
  ethernets:
    eth0:
      addresses: ['{config['ip_address']}/32']
      gateway4: {config['gateway']}
      nameservers:
        addresses: [{config['dns_servers']}]
      routes:
      - scope: link
        to: {config['gateway']}/32
        via: 0.0.0.0
  version: 2
"""
        with open('/tmp/01-netcfg.yaml', 'w') as f:
            f.write(netplan_config)

        run_command(f"pct push {config['vm_id']} /tmp/01-netcfg.yaml /etc/netplan/01-netcfg.yaml")
        run_command(f"pct exec {config['vm_id']} -- chmod 644 /etc/netplan/01-netcfg.yaml")
        run_command(f"pct exec {config['vm_id']} -- bash -c 'rm -f /etc/netplan/10-*.yaml'")
        run_command(f"pct exec {config['vm_id']} -- netplan apply")
        run_command("rm /tmp/01-netcfg.yaml")

    # Esperar a que el contenedor se inicie
    msg("Esperando a que el contenedor se inicie...")
    network_check_shown = False

    for attempt in range(30):
        time.sleep(5)

        status_json = run_command(f"pvesh get /nodes/{hostname_cmd}/lxc/{config['vm_id']}/status/current --output-format=json", exit_on_error=False)
        if status_json:
            status_data = json.loads(status_json)
            status = status_data.get('status')

            if status == "running":
                if not network_check_shown:
                    msg("El contenedor está en ejecución, verificando conectividad de red...")
                    network_check_shown = True

                ping_result = run_command(f"pct exec {config['vm_id']} -- ping -c 1 8.8.8.8", exit_on_error=False)
                if ping_result is not None:
                    break

        print(".", end="", flush=True)
    print("")

    if attempt >= 29:
        warning("La conectividad de red podría ser limitada. Continuando de todos modos...")

    success("Red OK, contenedor iniciado")

    # Copiar módulos personalizados al contenedor si están disponibles
    if custom_modules:
        section("CONFIGURACIÓN DE MÓDULOS PERSONALIZADOS")
        msg("Copiando módulos personalizados al contenedor...")
        
        # Crear un directorio temporal en el contenedor
        run_command(f"pct exec {config['vm_id']} -- mkdir -p /tmp/custom_modules")
        
        # Copiar cada módulo al contenedor
        for module in custom_modules:
            module_path = os.path.join(modules_dir, module)
            tmp_tar = f"/tmp/{module}.tar.gz"
            
            # Crear un archivo tar del módulo
            run_command(f"tar -czf {tmp_tar} -C {modules_dir} {module}")
            
            # Copiar el tar al contenedor
            run_command(f"pct push {config['vm_id']} {tmp_tar} /tmp/{module}.tar.gz")
            
            # Extraer en el contenedor
            run_command(f"pct exec {config['vm_id']} -- tar -xzf /tmp/{module}.tar.gz -C /tmp/custom_modules")
            
            # Limpiar
            run_command(f"rm {tmp_tar}")
            run_command(f"pct exec {config['vm_id']} -- rm /tmp/{module}.tar.gz")
            
            success(f"Módulo '{module}' transferido al contenedor")

    # Instalar Odoo
    section("INSTALACIÓN DE ODOO")
    msg(f"Instalando Odoo {config['odoo_version']}...")
    create_odoo_install_script(config['odoo_version'], config['db_password'], config['odoo_user'], custom_modules)
    run_command(f"pct push {config['vm_id']} /tmp/odoo_install.sh /root/odoo_install.sh")
    run_command(f"pct exec {config['vm_id']} -- chmod +x /root/odoo_install.sh")

    # Ejecutar el script de instalación con salida en tiempo real
    msg("Iniciando instalación de Odoo (esto puede tardar un tiempo)...")

    try:
        process = subprocess.Popen(
            f"pct exec {config['vm_id']} -- bash /root/odoo_install.sh",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        # Procesar y mostrar la salida en tiempo real
        for line in process.stdout:
            line = line.strip()
            if "[INFO]" in line:
                msg(line.replace("[INFO] ", ""), "INFO", "B")
            elif "[SUCCESS]" in line:
                success(line.replace("[SUCCESS] ", ""))
            elif "[WARNING]" in line:
                warning(line.replace("[WARNING] ", ""))
            elif "[ERROR]" in line:
                error(line.replace("[ERROR] ", ""))
            elif "[PROGRESS]" in line:
                msg(line.replace("[PROGRESS] ", ""), "PROGRESS", "O")
            else:
                print(f"  {line}")

        process.stdout.close()
        return_code = process.wait()

        if return_code != 0:
            warning(f"El proceso de instalación finalizó con código {return_code}")
        else:
            success("Instalación de Odoo completada con éxito")

    except Exception as e:
        error(f"Error durante la instalación: {str(e)}")

    run_command("rm /tmp/odoo_install.sh")

    # Mostrar información final
    section("INSTALACIÓN COMPLETADA")
    print(f"{C['O']}╔═════════════════════════════════════════════════════════════════╗{C['N']}")
    print(f"{C['O']}║                   C3i SERVICIOS INFORMÁTICOS                    ║{C['N']}")
    print(f"{C['O']}║                 INSTALACIÓN DE ODOO COMPLETADA                  ║{C['N']}")
    print(f"{C['O']}╚═════════════════════════════════════════════════════════════════╝{C['N']}")

    show_group("Información de acceso a Odoo")
    show_item("URL", f"http://{config['ip_address']}:8069")
    show_item("Usuario de base de datos", config['odoo_user'])
    show_item("Contraseña de base de datos", config['db_password'])
    print("")

    show_group("Acceso al contenedor")
    show_item("Comando SSH", f"ssh root@{config['ip_address']}")
    show_item("Contraseña SSH", config['password'])
    show_item("Desde Proxmox", f"pct enter {config['vm_id']}")
    
    if custom_modules:
        print("")
        show_group("Módulos personalizados")
        for module in custom_modules:
            show_item("Módulo instalado", module)
        
        print(f"\n{C['Y']}NOTA: Los módulos personalizados estarán disponibles después de crear la base de datos.{C['N']}")
        print(f"{C['Y']}      Deberá activarlos desde el menú de Aplicaciones en Odoo.{C['N']}")
        
    print(f"\n{C['Y']}NOTA: Espere unos minutos para que Odoo se inicialice completamente.{C['N']}\n")

if __name__ == "__main__":
    main()

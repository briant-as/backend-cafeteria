import datetime
import hmac
import json
import os
from typing import List, Optional
from zoneinfo import ZoneInfo

import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# =======================================================
# ----- CONFIGURACIÓN (variables de entorno en Render) -----
# =======================================================
# ADMIN_TOKEN / ADMIN_PASSWORD: si no existen, usa "admin123" (igual que hoy).
# FIREBASE_CREDENTIALS_JSON: contenido completo del JSON de la cuenta de servicio.
#   Si no existe, intenta leer el archivo local credenciales.json (solo para desarrollo).
# PRECIOS_ESTRICTOS: "1" (default) exige que cada item venga con producto_id y
#   recalcula el precio contra la base. Poné "0" SOLO mientras migrás los HTML.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin123")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", ADMIN_TOKEN)
PRECIOS_ESTRICTOS = os.environ.get("PRECIOS_ESTRICTOS", "1") == "1"

RECARGOS_LECHE = {0, 500, 600}          # sin recargo, almendras, avena
ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")
MAX_CANTIDAD_POR_ITEM = 50

cred_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
if cred_json:
    cred = credentials.Certificate(json.loads(cred_json))
else:
    cred = credentials.Certificate("credenciales.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

app = FastAPI(title="Sistema de Pedidos - Cafetería")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def requiere_token(x_token: str = Header(default="")):
    """Protege las rutas que modifican datos. Exige la cabecera x-token."""
    if not hmac.compare_digest(x_token.encode(), ADMIN_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="No autorizado.")


# =======================================================
# ----- MODELOS -----
# =======================================================
class ItemPedido(BaseModel):
    producto: str
    cantidad: int
    precio_unitario: float
    producto_id: str = ""   # id del documento en "productos" (los avisos al mozo no lo llevan)


class LoginRequest(BaseModel):
    clave: str


class NuevoProducto(BaseModel):
    nombre: str
    precio: int
    descripcion: str = ""
    categoria: str = "Cafetería"
    lleva_leche: bool = False
    imagen: str = ""
    stock: Optional[int] = Field(default=None, ge=0)   # None = sin control de stock


class ActualizarStock(BaseModel):
    stock: Optional[int] = Field(default=None, ge=0)   # None = quitar control de stock


class Pedido(BaseModel):
    mesa: int
    items: List[ItemPedido]
    notas: str = ""


# =======================================================
# ----- CATÁLOGO -----
# =======================================================
@app.get("/obtener_catalogo")
async def obtener_catalogo():
    referencia = db.collection("productos").stream()
    lista_productos = []
    for doc in referencia:
        producto_db = doc.to_dict()
        stock = producto_db.get("stock")   # None = ilimitado
        lista_productos.append({
            "id": doc.id,
            "producto": producto_db.get("nombre", "Sin nombre"),
            "precio": producto_db.get("precio", 0),
            "descripcion": producto_db.get("descripcion", ""),
            "categoria": producto_db.get("categoria", "Cafetería"),
            "lleva_leche": producto_db.get("lleva_leche", False),
            "imagen": producto_db.get("imagen", ""),
            "stock": stock,
            "agotado": stock is not None and stock <= 0
        })
    return {"catalogo": lista_productos}


@app.post("/verificar_acceso")
async def verificar_acceso(req: LoginRequest):
    if hmac.compare_digest(req.clave.encode(), ADMIN_PASSWORD.encode()):
        return {"acceso": True}
    return {"acceso": False}


@app.post("/agregar_producto", dependencies=[Depends(requiere_token)])
async def agregar_producto(producto: NuevoProducto):
    nuevo_prod_db = {
        "nombre": producto.nombre, "precio": producto.precio,
        "descripcion": producto.descripcion, "categoria": producto.categoria,
        "lleva_leche": producto.lleva_leche, "imagen": producto.imagen,
        "stock": producto.stock
    }
    db.collection("productos").add(nuevo_prod_db)
    return {"status": "éxito"}


@app.put("/actualizar_stock/{producto_id}", dependencies=[Depends(requiere_token)])
async def actualizar_stock(producto_id: str, datos: ActualizarStock):
    ref = db.collection("productos").document(producto_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    ref.update({"stock": datos.stock})
    return {"status": "éxito", "stock": datos.stock}


@app.delete("/borrar_producto/{producto_id}", dependencies=[Depends(requiere_token)])
async def borrar_producto(producto_id: str):
    db.collection("productos").document(producto_id).delete()
    return {"status": "éxito"}


# =======================================================
# ----- SISTEMA DE CACHÉ Y CONTROL DE MESAS -----
# =======================================================
memoria_pedidos = []
memoria_desactualizada = True

memoria_mesas = {}
mesas_desactualizadas = True


def refrescar_datos():
    global memoria_pedidos, memoria_desactualizada, memoria_mesas, mesas_desactualizadas

    if memoria_desactualizada:
        ref = db.collection("pedidos").stream()
        lista_pedidos = []
        for doc in ref:
            pedido_db = doc.to_dict()
            lista_pedidos.append({
                "id": doc.id, "mesa": pedido_db.get("mesa", 0),
                "items": pedido_db.get("items", []), "notas": pedido_db.get("notas", ""),
                "estado": pedido_db.get("estado", "pendiente"), "hora": pedido_db.get("hora", "")
            })
        memoria_pedidos = lista_pedidos
        memoria_desactualizada = False

    if mesas_desactualizadas:
        ref_mesas = db.collection("mesas").stream()
        nuevas_mesas = {}
        for doc in ref_mesas:
            nuevas_mesas[doc.id] = doc.to_dict().get("abierta", False)
        memoria_mesas = nuevas_mesas
        mesas_desactualizadas = False


@app.get("/obtener_mesas")
async def obtener_mesas():
    refrescar_datos()
    return {"mesas": memoria_mesas}


@app.put("/actualizar_mesa/{numero_mesa}/{estado}", dependencies=[Depends(requiere_token)])
async def actualizar_mesa(numero_mesa: str, estado: str):
    global mesas_desactualizadas
    es_abierta = True if estado.lower() == 'true' else False
    db.collection("mesas").document(numero_mesa).set({"abierta": es_abierta})
    mesas_desactualizadas = True
    return {"status": "éxito"}


@app.get("/obtener_pedidos")
async def obtener_pedidos():
    refrescar_datos()
    return {"pedidos": memoria_pedidos}


# =======================================================
# ----- CREACIÓN DE PEDIDOS (con descuento de stock atómico) -----
# =======================================================
class ErrorPedido(Exception):
    def __init__(self, status: int, detalle: str):
        self.status = status
        self.detalle = detalle


def _procesar_pedido(pedido: Pedido, hora: str):
    """
    Dentro de UNA transacción de Firestore:
      1) lee los productos involucrados,
      2) valida precios contra la base,
      3) verifica y descuenta stock,
      4) guarda el pedido.
    Si algo falla, no se descuenta nada ni se crea el pedido.
    Dos clientes pidiendo la última unidad al mismo tiempo: uno solo la consigue.
    """
    es_aviso = pedido.notas == "Aviso automático"

    cantidades = {}
    for it in pedido.items:
        if it.producto_id:
            cantidades[it.producto_id] = cantidades.get(it.producto_id, 0) + it.cantidad

    refs = {pid: db.collection("productos").document(pid) for pid in cantidades}
    pedido_ref = db.collection("pedidos").document()

    @firestore.transactional
    def _tx(tx):
        snaps = {pid: ref.get(transaction=tx) for pid, ref in refs.items()}

        items_final = []
        total = 0.0

        for it in pedido.items:
            if not it.producto_id:
                # Solo se admite sin producto_id el aviso al mozo/cuenta (precio 0)
                aviso_valido = es_aviso and it.precio_unitario == 0 and it.cantidad == 1
                if PRECIOS_ESTRICTOS and not aviso_valido:
                    raise ErrorPedido(400, "El menú está desactualizado. Recargá la página e intentá de nuevo.")
                items_final.append(it.model_dump())
                total += it.cantidad * it.precio_unitario
                continue

            snap = snaps[it.producto_id]
            if not snap.exists:
                raise ErrorPedido(400, f"'{it.producto}' ya no está disponible. Recargá el menú.")

            prod = snap.to_dict()
            recargo = it.precio_unitario - prod.get("precio", 0)
            permitidos = RECARGOS_LECHE if prod.get("lleva_leche") else {0}
            if recargo not in permitidos:
                raise ErrorPedido(400, f"El precio de '{prod.get('nombre', it.producto)}' cambió. Recargá el menú.")

            items_final.append(it.model_dump())
            total += it.cantidad * it.precio_unitario

        # Verificación de stock (todo o nada)
        faltantes = []
        nuevos_stock = {}
        for pid, cant in cantidades.items():
            prod = snaps[pid].to_dict()
            stock = prod.get("stock")
            if stock is None:
                continue  # producto sin control de stock
            if stock < cant:
                faltantes.append(f"{prod.get('nombre', 'Producto')} (quedan {max(stock, 0)})")
            else:
                nuevos_stock[pid] = stock - cant

        if faltantes:
            raise ErrorPedido(409, "Sin stock suficiente: " + ", ".join(faltantes) + ". Ajustá tu pedido.")

        for pid, nuevo in nuevos_stock.items():
            tx.update(refs[pid], {"stock": nuevo})

        tx.set(pedido_ref, {
            "mesa": pedido.mesa, "items": items_final,
            "notas": pedido.notas, "total": total,
            "estado": "pendiente", "hora": hora
        })

    _tx(db.transaction())


@app.post("/crear_pedido")
async def recibir_pedido(pedido: Pedido):
    global memoria_desactualizada
    refrescar_datos()

    # 1. BLOQUEO ANTI-TROLLS: la mesa debe estar abierta
    mesa_abierta = memoria_mesas.get(str(pedido.mesa), False)
    if not mesa_abierta:
        raise HTTPException(status_code=403, detail="Mesa inactiva. Por favor, hacele una seña al mozo para que te habilite la mesa.")

    # 2. VALIDACIONES BÁSICAS
    if len(pedido.items) == 0:
        raise HTTPException(status_code=400, detail="Carrito vacío.")
    for item in pedido.items:
        if item.cantidad <= 0 or item.cantidad > MAX_CANTIDAD_POR_ITEM or item.precio_unitario < 0:
            raise HTTPException(status_code=400, detail="Cantidades inválidas.")

    hora_actual = datetime.datetime.now(ZONA_HORARIA).strftime("%H:%M")

    # 3. TRANSACCIÓN (precios + stock + guardado)
    try:
        await run_in_threadpool(_procesar_pedido, pedido, hora_actual)
    except ErrorPedido as e:
        raise HTTPException(status_code=e.status, detail=e.detalle)

    memoria_desactualizada = True
    return {"status": "éxito"}


@app.put("/actualizar_pedido/{pedido_id}/{nuevo_estado}", dependencies=[Depends(requiere_token)])
async def actualizar_pedido(pedido_id: str, nuevo_estado: str):
    global memoria_desactualizada
    db.collection("pedidos").document(pedido_id).update({"estado": nuevo_estado})
    memoria_desactualizada = True
    return {"status": "éxito"}


@app.delete("/limpiar_historial", dependencies=[Depends(requiere_token)])
async def limpiar_historial():
    global memoria_desactualizada
    referencia = db.collection("pedidos").stream()
    for doc in referencia:
        pedido = doc.to_dict()
        if pedido.get("estado") == "entregado":
            db.collection("pedidos").document(doc.id).delete()
    memoria_desactualizada = True
    return {"status": "éxito"}

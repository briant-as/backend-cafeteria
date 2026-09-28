import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

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

class ItemPedido(BaseModel):
    producto: str
    cantidad: int
    precio_unitario: float

class LoginRequest(BaseModel):
    clave: str

class NuevoProducto(BaseModel):
    nombre: str
    precio: int
    descripcion: str = ""
    categoria: str = "Cafetería"
    lleva_leche: bool = False
    imagen: str = ""

class Pedido(BaseModel):
    mesa: int
    items: List[ItemPedido]
    notas: str = ""

@app.get("/obtener_catalogo")
async def obtener_catalogo():
    referencia = db.collection("productos").stream()
    lista_productos = []
    for doc in referencia:
        producto_db = doc.to_dict()
        lista_productos.append({
            "id": doc.id,
            "producto": producto_db.get("nombre", "Sin nombre"),
            "precio": producto_db.get("precio", 0),
            "descripcion": producto_db.get("descripcion", ""),
            "categoria": producto_db.get("categoria", "Cafetería"),
            "lleva_leche": producto_db.get("lleva_leche", False),
            "imagen": producto_db.get("imagen", "")
        })
    return {"catalogo": lista_productos}

@app.post("/verificar_acceso")
async def verificar_acceso(req: LoginRequest):
    CLAVE_SECRETA = "admin123"
    if req.clave == CLAVE_SECRETA:
        return {"acceso": True}
    return {"acceso": False}
        
@app.post("/agregar_producto")
async def agregar_producto(producto: NuevoProducto):
    nuevo_prod_db = {
        "nombre": producto.nombre, "precio": producto.precio,
        "descripcion": producto.descripcion, "categoria": producto.categoria,
        "lleva_leche": producto.lleva_leche, "imagen": producto.imagen
    }
    db.collection("productos").add(nuevo_prod_db)
    return {"status": "éxito"}

@app.delete("/borrar_producto/{producto_id}")
async def borrar_producto(producto_id: str):
    db.collection("productos").document(producto_id).delete()
    return {"status": "éxito"}

# =======================================================
# ----- SISTEMA DE CACHÉ Y CONTROL DE MESAS -----
# =======================================================

memoria_pedidos = []
memoria_desactualizada = True 

# Nueva memoria para las mesas
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

@app.put("/actualizar_mesa/{numero_mesa}/{estado}")
async def actualizar_mesa(numero_mesa: str, estado: str):
    global mesas_desactualizadas
    # Convertimos el string 'true' o 'false' a booleano de Python
    es_abierta = True if estado.lower() == 'true' else False
    db.collection("mesas").document(numero_mesa).set({"abierta": es_abierta})
    mesas_desactualizadas = True
    return {"status": "éxito"}

@app.get("/obtener_pedidos")
async def obtener_pedidos():
    refrescar_datos()
    return {"pedidos": memoria_pedidos}

@app.post("/crear_pedido")
async def recibir_pedido(pedido: Pedido):
    global memoria_desactualizada
    refrescar_datos() # Nos aseguramos de tener el estado de las mesas al día

    # 1. BLOQUEO ANTI-TROLLS: Verificar si la mesa está abierta
    # (Por defecto, si una mesa no existe en la base de datos, está False/Cerrada)
    mesa_str = str(pedido.mesa)
    mesa_abierta = memoria_mesas.get(mesa_str, False)
    
    if not mesa_abierta:
        raise HTTPException(status_code=403, detail="Mesa inactiva. Por favor, hacele una seña al mozo para que te habilite la mesa.")

    # 2. VALIDACIONES DE SEGURIDAD (Cantidades negativas)
    for item in pedido.items:
        if item.cantidad <= 0 or item.precio_unitario < 0:
            raise HTTPException(status_code=400, detail="Cantidades inválidas.")
    if len(pedido.items) == 0:
        raise HTTPException(status_code=400, detail="Carrito vacío.")

    total_calculado = sum(item.cantidad * item.precio_unitario for item in pedido.items)
    hora_actual = datetime.datetime.now().strftime("%H:%M")
    
    nuevo_pedido_db = {
        "mesa": pedido.mesa, "items": [item.model_dump() for item in pedido.items],
        "notas": pedido.notas, "total": total_calculado,
        "estado": "pendiente", "hora": hora_actual
    }

    db.collection("pedidos").add(nuevo_pedido_db)
    memoria_desactualizada = True
    
    return {"status": "éxito"}

@app.put("/actualizar_pedido/{pedido_id}/{nuevo_estado}")
async def actualizar_pedido(pedido_id: str, nuevo_estado: str):
    global memoria_desactualizada
    db.collection("pedidos").document(pedido_id).update({"estado": nuevo_estado})
    memoria_desactualizada = True
    return {"status": "éxito"}

@app.delete("/limpiar_historial")
async def limpiar_historial():
    global memoria_desactualizada
    referencia = db.collection("pedidos").stream()
    for doc in referencia:
        pedido = doc.to_dict()
        if pedido.get("estado") == "entregado":
            db.collection("pedidos").document(doc.id).delete()
    memoria_desactualizada = True
    return {"status": "éxito"}
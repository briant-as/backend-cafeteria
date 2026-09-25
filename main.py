import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

# 1. Inicializamos la conexión con Firebase
# El archivo credenciales.json debe estar en la misma carpeta que este main.py
cred = credentials.Certificate("credenciales.json")
firebase_admin.initialize_app(cred)

# Creamos el cliente para interactuar con la base de datos
db = firestore.client()

app = FastAPI(title="Sistema de Pedidos - Cafetería")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En producción se pone el dominio real, acá permitimos todos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ItemPedido(BaseModel):
    producto: str
    cantidad: int
    precio_unitario: float

class NuevoProducto(BaseModel):
    nombre: str
    precio: int
    descripcion: str = ""
    categoria: str = "Cafetería"

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
            "categoria": producto_db.get("categoria", "Cafetería")
        })
        
    return {"catalogo": lista_productos}
        
@app.post("/agregar_producto")
async def agregar_producto(producto: NuevoProducto):
    nuevo_prod_db = {
        "nombre": producto.nombre,
        "precio": producto.precio,
        "descripcion": producto.descripcion,
        "categoria": producto.categoria
    }
    db.collection("productos").add(nuevo_prod_db)
    return {"status": "éxito", "mensaje": "Producto agregado al catálogo"}
@app.post("/crear_pedido")
async def recibir_pedido(pedido: Pedido):
    total_calculado = sum(item.cantidad * item.precio_unitario for item in pedido.items)
    
    nuevo_pedido_db = {
        "mesa": pedido.mesa,
        "items": [item.model_dump() for item in pedido.items],
        "notas": pedido.notas,
        "total": total_calculado,
        "estado": "pendiente"
    }

    # 2. Guardamos el pedido en la colección 'pedidos' de Firestore
    # add() devuelve la fecha de creación y la referencia del documento creado
    hora_creacion, ref_documento = db.collection("pedidos").add(nuevo_pedido_db)
    
    print(f"Pedido guardado en Firestore con ID: {ref_documento.id}")

    
    return {
        "status": "éxito",
        "mensaje": "Pedido enviado a la cocina",
        "pedido_id": ref_documento.id
    }
@app.delete("/borrar_producto/{producto_id}")
async def borrar_producto(producto_id: str):
    # Buscamos el documento por su ID exacto y lo eliminamos de la base de datos
    db.collection("productos").document(producto_id).delete()
    return {"status": "éxito", "mensaje": "Producto eliminado"}
package MODELO;

public class OrdenReparacionRepuesto {

    private OrdenReparacion orden;
    private Repuesto repuesto;
    private int cantidad;

    public OrdenReparacionRepuesto() {
    }

    public OrdenReparacionRepuesto(OrdenReparacion orden, Repuesto repuesto, int cantidad) {
        this.orden = orden;
        this.repuesto = repuesto;
        this.cantidad = cantidad;
    }

    public OrdenReparacion getOrden() {
        return orden;
    }

    public void setOrden(OrdenReparacion orden) {
        this.orden = orden;
    }

    public Repuesto getRepuesto() {
        return repuesto;
    }

    public void setRepuesto(Repuesto repuesto) {
        this.repuesto = repuesto;
    }

    public int getCantidad() {
        return cantidad;
    }

    public void setCantidad(int cantidad) {
        this.cantidad = cantidad;
    }
}

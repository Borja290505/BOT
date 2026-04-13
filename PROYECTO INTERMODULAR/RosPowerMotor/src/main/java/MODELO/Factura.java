package MODELO;

import java.time.LocalDate;

public class Factura {

    private int idFactura;
    private LocalDate fechaFactura;
    private double subtotal;
    private double iva;
    private double total;

    private OrdenReparacion orden;

    public Factura() {
    }

    public Factura(int idFactura, LocalDate fechaFactura, double subtotal, double iva, double total, OrdenReparacion orden) {
        this.idFactura = idFactura;
        this.fechaFactura = fechaFactura;
        this.subtotal = subtotal;
        this.iva = iva;
        this.total = total;
        this.orden = orden;
    }

    public int getIdFactura() {
        return idFactura;
    }

    public void setIdFactura(int idFactura) {
        this.idFactura = idFactura;
    }

    public LocalDate getFechaFactura() {
        return fechaFactura;
    }

    public void setFechaFactura(LocalDate fechaFactura) {
        this.fechaFactura = fechaFactura;
    }

    public double getSubtotal() {
        return subtotal;
    }

    public void setSubtotal(double subtotal) {
        this.subtotal = subtotal;
    }

    public double getIva() {
        return iva;
    }

    public void setIva(double iva) {
        this.iva = iva;
    }

    public double getTotal() {
        return total;
    }

    public void setTotal(double total) {
        this.total = total;
    }

    public OrdenReparacion getOrden() {
        return orden;
    }

    public void setOrden(OrdenReparacion orden) {
        this.orden = orden;
    }
}

package MODELO;

import java.time.LocalDate;

public class OrdenReparacion {

    private int idOrden;
    private LocalDate fechaApertura;
    private LocalDate fechaCierre;
    private int kmEntrada;
    private String estado;
    private String observaciones;

    private Vehiculo vehiculo;
    private List<OrdenReparacionRepuesto> repuestosUsados;

    public OrdenReparacion() {
    }

    public OrdenReparacion(int idOrden, LocalDate fechaApertura, LocalDate fechaCierre, int kmEntrada, String estado, String observaciones, Vehiculo vehiculo, List<OrdenReparacionRepuesto> repuestosUsados) {
        this.idOrden = idOrden;
        this.fechaApertura = fechaApertura;
        this.fechaCierre = fechaCierre;
        this.kmEntrada = kmEntrada;
        this.estado = estado;
        this.observaciones = observaciones;
        this.vehiculo = vehiculo;
        this.repuestosUsados = repuestosUsados;
    }

    public int getIdOrden() {
        return idOrden;
    }

    public void setIdOrden(int idOrden) {
        this.idOrden = idOrden;
    }

    public LocalDate getFechaApertura() {
        return fechaApertura;
    }

    public void setFechaApertura(LocalDate fechaApertura) {
        this.fechaApertura = fechaApertura;
    }

    public LocalDate getFechaCierre() {
        return fechaCierre;
    }

    public void setFechaCierre(LocalDate fechaCierre) {
        this.fechaCierre = fechaCierre;
    }

    public int getKmEntrada() {
        return kmEntrada;
    }

    public void setKmEntrada(int kmEntrada) {
        this.kmEntrada = kmEntrada;
    }

    public String getEstado() {
        return estado;
    }

    public void setEstado(String estado) {
        this.estado = estado;
    }

    public String getObservaciones() {
        return observaciones;
    }

    public void setObservaciones(String observaciones) {
        this.observaciones = observaciones;
    }

    public Vehiculo getVehiculo() {
        return vehiculo;
    }

    public void setVehiculo(Vehiculo vehiculo) {
        this.vehiculo = vehiculo;
    }

    public List<OrdenReparacionRepuesto> getRepuestosUsados() {
        return repuestosUsados;
    }

    public void setRepuestosUsados(List<OrdenReparacionRepuesto> repuestosUsados) {
        this.repuestosUsados = repuestosUsados;
    }
}

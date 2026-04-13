package MODELO;

public class Repuesto {

    private int idRepuesto;
    private String nombre;
    private String marca;
    private double precioUnitario;
    private int stock;

    public Repuesto() {
    }

    public Repuesto(int idRepuesto, String nombre, String marca, double precioUnitario, int stock) {
        this.idRepuesto = idRepuesto;
        this.nombre = nombre;
        this.marca = marca;
        this.precioUnitario = precioUnitario;
        this.stock = stock;
    }

    public int getIdRepuesto() {
        return idRepuesto;
    }

    public void setIdRepuesto(int idRepuesto) {
        this.idRepuesto = idRepuesto;
    }

    public String getNombre() {
        return nombre;
    }

    public void setNombre(String nombre) {
        this.nombre = nombre;
    }

    public String getMarca() {
        return marca;
    }

    public void setMarca(String marca) {
        this.marca = marca;
    }

    public double getPrecioUnitario() {
        return precioUnitario;
    }

    public void setPrecioUnitario(double precioUnitario) {
        this.precioUnitario = precioUnitario;
    }

    public int getStock() {
        return stock;
    }

    public void setStock(int stock) {
        this.stock = stock;
    }
}

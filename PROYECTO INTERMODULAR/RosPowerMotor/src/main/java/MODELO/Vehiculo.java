package MODELO;

public class Vehiculo {

    private String matricula;
    private String bastidor;
    private String marca;
    private String modelo;
    private int anio;
    private int kmsActuales;

    private Cliente cliente;

    public Vehiculo() {
    }

    public Vehiculo(String matricula, String bastidor, String marca, String modelo, int anio, int kmsActuales, Cliente cliente) {
        this.matricula = matricula;
        this.bastidor = bastidor;
        this.marca = marca;
        this.modelo = modelo;
        this.anio = anio;
        this.kmsActuales = kmsActuales;
        this.cliente = cliente;
    }

    public String getMatricula() {
        return matricula;
    }

    public void setMatricula(String matricula) {
        this.matricula = matricula;
    }

    public String getBastidor() {
        return bastidor;
    }

    public void setBastidor(String bastidor) {
        this.bastidor = bastidor;
    }

    public String getMarca() {
        return marca;
    }

    public void setMarca(String marca) {
        this.marca = marca;
    }

    public String getModelo() {
        return modelo;
    }

    public void setModelo(String modelo) {
        this.modelo = modelo;
    }

    public int getAnio() {
        return anio;
    }

    public void setAnio(int anio) {
        this.anio = anio;
    }

    public int getKmsActuales() {
        return kmsActuales;
    }

    public void setKmsActuales(int kmsActuales) {
        this.kmsActuales = kmsActuales;
    }

    public Cliente getCliente() {
        return cliente;
    }

    public void setCliente(Cliente cliente) {
        this.cliente = cliente;
    }
}

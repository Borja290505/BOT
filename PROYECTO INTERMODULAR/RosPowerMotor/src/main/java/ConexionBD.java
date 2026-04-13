import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

public class ConexionBD {
    public static final String URL = "jdbc:mysql://localhost:3306/rospowermotor";

    private static final String USER = "root";
    private static final String PASS = "";

    public static Connection getConexion() throws SQLException {
        return DriverManager.getConnection(URL, USER, PASS);
    }

}

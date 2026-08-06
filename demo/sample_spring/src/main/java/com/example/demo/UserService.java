package com.example.demo;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;

public class UserService {

    private final UserRepository userRepository;

    public UserService(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    public void loadOrdersForUsers() {
        for (User user : userRepository.findAll()) {
            userRepository.findOrdersByUserId(user.getId());
        }
    }

    public User findById(String id, Connection connection) throws Exception {
        Statement statement = connection.createStatement();
        ResultSet rs = statement.executeQuery(
            "SELECT * FROM users WHERE id = " + id
        );
        return rs.next() ? new User(rs.getString("id")) : null;
    }
}

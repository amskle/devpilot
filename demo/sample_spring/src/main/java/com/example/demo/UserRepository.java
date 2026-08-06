package com.example.demo;

import java.util.List;

public interface UserRepository {
    List<User> findAll();
    List<Order> findOrdersByUserId(String userId);
}
